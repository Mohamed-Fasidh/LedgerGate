# Private document storage

## Supported backends

LedgerGate supports two document-storage backends:

- `local`: development and test use only. Files are written beneath `UPLOAD_DIR` using opaque tenant-scoped keys. Tenant directories are created with mode `0700` and files with mode `0600` where the host filesystem supports POSIX permissions.
- `s3`: required when `APP_ENV=production`. Objects are stored beneath a tenant prefix with a random server-generated name. Uploads request S3 server-side encryption with AES-256 (`ServerSideEncryption=AES256`). Downloads are generated as short-lived SigV4 presigned GET URLs and are redirected only after the API has checked invoice ownership against the authenticated tenant.

The API never accepts a client-supplied storage key or bucket name. Treat object keys as internal identifiers, not authorization tokens.

## Configuration

Set these environment variables in the API deployment:

| Variable | Purpose |
| --- | --- |
| `APP_ENV` | Set to `production` for production validation |
| `STORAGE_BACKEND` | Set to `s3` in production |
| `S3_BUCKET` | Private bucket name |
| `S3_REGION` | AWS region, e.g. `ap-south-1` |
| `S3_ENDPOINT_URL` | Optional S3-compatible endpoint; HTTPS required in production |
| `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | Optional static credentials; configure both or neither |
| `S3_FORCE_PATH_STYLE` | Enable path-style addressing for compatible object stores |
| `S3_PRESIGNED_URL_EXPIRY_SECONDS` | Download URL lifetime, constrained to 60–900 seconds |

When static credentials are omitted, boto3 uses its standard credential provider chain (for example, an attached workload role). Prefer workload identity/instance roles over long-lived access keys. Do not commit credentials or place them in frontend variables.

## Bucket and IAM controls

Before production use:

1. Create a dedicated bucket for LedgerGate documents. Keep all public-access-block settings enabled and do not configure public website hosting.
2. Require TLS for all bucket requests with a bucket policy that denies requests where `aws:SecureTransport` is false.
3. Give the API identity only the permissions it needs on this bucket/prefix: `s3:PutObject`, `s3:GetObject`, and `s3:DeleteObject` for document objects, plus `s3:ListBucket` only if an operational workflow requires it. Do not grant bucket administration or public ACL permissions.
4. Enable bucket versioning if recovery requirements call for it. Define lifecycle and retention rules with the data owner and legal/privacy requirements; do not apply automatic expiration without an approved retention schedule.
5. Consider a customer-managed KMS key instead of AES-256 SSE-S3 where key separation, audit, or regulatory requirements demand it. The current code requests AES-256 SSE-S3 and does not yet configure SSE-KMS.
6. Monitor object access and policy changes. Configure alerts for public-access changes, unusual downloads, denied requests, and credential use outside expected workloads.

The application requests encryption on upload but cannot prove the bucket policy, public-access block, IAM scope, or lifecycle configuration. Verify those settings in the cloud account before storing real documents.

## Upload and failure behavior

The API validates the file extension, size, basic PDF signature or UTF-8 text content, and extraction page limit. It writes the object before committing the invoice row. If the database persistence step fails, it rolls back and attempts to delete the newly written object. Cleanup is best-effort: a storage outage or permission error can leave an orphaned object. Monitor and periodically reconcile objects against database references before enabling large-scale use.

Malware scanning, content disarm/reconstruction, parser sandboxing, advanced file signature detection, and automated orphan reconciliation are not implemented. Do not expose invoice upload to untrusted public traffic until those controls are deployed.

## Validation and release checks

The test suite mocks S3 calls and verifies encryption parameters, tenant-prefix checks, short-lived download URL expiry, and object deletion behavior. These tests do not contact AWS or prove a real bucket's policy. Before release, run an isolated integration test against a private non-production bucket, verify cross-tenant download denial through the API, confirm deletion permissions, and inspect CloudTrail or equivalent object-access logs.


## Optional real-bucket integration test

The default CI suite mocks S3 and does not require cloud credentials. To exercise the real provider adapter against a dedicated **non-production test bucket**, configure the normal S3 environment variables and run:

```bash
cd backend
LEDGERGATE_S3_INTEGRATION=1 pytest -q tests/test_storage.py -k real_s3
```

The test uploads a uniquely named tenant-scoped object, reads it back, checks server-side encryption metadata, creates a short-lived presigned URL, and deletes the object in a `finally` block. The test identity needs only the bucket permissions required for object upload, read/head, and delete. Do not point this test at a production bucket.
