# API examples

Register:
```bash
curl -X POST http://localhost:8000/api/v1/auth/register -H 'Content-Type: application/json' -d '{"email":"admin@example.com","password":"change-this-password-123","tenant_name":"Acme"}'
```
Use the returned token:
```bash
TOKEN='paste-token'
curl -X POST http://localhost:8000/api/v1/suppliers -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"name":"Acme Supplies","tax_id":"GSTIN-EXAMPLE"}'
```
Create a PO (replace supplier_id):
```bash
curl -X POST http://localhost:8000/api/v1/purchase-orders -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"po_number":"PO-1001","supplier_id":1,"currency":"INR","total_amount":50000}'
```
Create an invoice (replace IDs):
```bash
curl -X POST http://localhost:8000/api/v1/invoices -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"invoice_number":"INV-1001","supplier_id":1,"purchase_order_id":1,"currency":"INR","amount":65000}'
```
Reconcile:
```bash
curl -X POST http://localhost:8000/api/v1/invoices/1/reconcile -H "Authorization: Bearer $TOKEN"
```

## Bounded collection listing

Tenant collection endpoints return at most 100 records by default. Use `limit` (1–200) and `offset` (zero or greater) to page through results. Every query remains scoped to the authenticated tenant. The same pagination parameters are available on suppliers, purchase orders, invoices, approval policies, workspace users, and audit events.

```bash
# First 100 invoices
curl 'http://localhost:8000/api/v1/invoices?limit=100&offset=0' \
  -H "Authorization: Bearer $TOKEN"

# Next page, filtered to pending invoices
curl 'http://localhost:8000/api/v1/invoices?status=pending&limit=100&offset=100' \
  -H "Authorization: Bearer $TOKEN"

# Suppliers and purchase orders
curl 'http://localhost:8000/api/v1/suppliers?limit=50&offset=0' \
  -H "Authorization: Bearer $TOKEN"
curl 'http://localhost:8000/api/v1/purchase-orders?limit=50&offset=0' \
  -H "Authorization: Bearer $TOKEN"

# Workspace members (administrator) and audit history
curl 'http://localhost:8000/api/v1/users?limit=100&offset=0' \
  -H "Authorization: Bearer $TOKEN"
curl 'http://localhost:8000/api/v1/audit-events?limit=100&offset=100' \
  -H "Authorization: Bearer $TOKEN"
```

Invalid limits (outside 1–200) or negative offsets return HTTP 400. Responses remain JSON arrays for compatibility with existing clients.

Upload an invoice with an idempotency key (retry with the same key, filename, and file bytes if the response is lost):
```bash
curl -X POST http://localhost:8000/api/v1/invoices/upload \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Idempotency-Key: upload-invoice-2026-0001' \
  -F 'file=@./invoice.pdf'
```
The upload key is scoped to the tenant and shared with invoice-creation keys. Repeating the same upload request returns the original invoice without creating another stored file. Reusing the key for a different file or a different invoice operation returns HTTP 409. Keys must contain 8-128 printable ASCII characters.

Create an amount-based approval policy as an administrator:
```bash
curl -X POST http://localhost:8000/api/v1/approval-policies \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"High value invoices","min_amount":100000,"max_amount":null,"approval_steps":[["reviewer"],["admin"]]}'
```
The example requires a reviewer approval followed by a different administrator approval for invoices of INR 100,000 or more. Amount ranges use an exclusive maximum; active policies for a tenant cannot overlap. The selected policy is snapshotted on the invoice when its first decision is submitted, so later policy changes do not rewrite an in-flight approval.

Submit an approval at the current stage (the authenticated user must have a role allowed by that stage and must not be the invoice creator):
```bash
curl -X POST http://localhost:8000/api/v1/invoices/1/approve \
  -H "Authorization: Bearer $REVIEWER_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"comment":"Matched against the purchase order."}'
```
A successful response either advances the invoice to the next stage or marks it approved after the final stage. Inspect the recorded decisions:
```bash
curl http://localhost:8000/api/v1/invoices/1/approvals \
  -H "Authorization: Bearer $TOKEN"
```



## Tenant member administration

A workspace administrator can create analyst or reviewer accounts with an initial password. This endpoint does not send an email invitation; share the initial credential through a separate secure channel and require the member to change it through your organization's onboarding process before using the account for sensitive work.

```bash
curl -X POST 'http://localhost:8000/api/v1/users?email=reviewer@example.com' \\
  -H "Authorization: Bearer $ADMIN_TOKEN" \\
  -H 'Idempotency-Key: create-reviewer-2026-0001' \\
  -F 'password=replace-with-a-unique-12-character-or-longer-password' \\
  -F 'role=reviewer'
```

Administrators can list workspace members with `GET /api/v1/users`, update an active non-admin member's role with `PATCH /api/v1/users/{user_id}/role` and JSON `{"role":"analyst"}` or `{"role":"reviewer"}`, and deactivate a non-admin member with `POST /api/v1/users/{user_id}/deactivate`. Role changes and deactivation revoke that member's active refresh sessions and write audit events. Administrator accounts are protected from these role/deactivation endpoints; self-deactivation is blocked. Deactivation does not invalidate access tokens already issued, which remain usable until their short expiry.

## Tenant-wide idempotency

The `Idempotency-Key` namespace is shared by invoice creation, invoice uploads, and the other supported state-changing API operations within each tenant. A key already reserved by a different operation or request returns HTTP 409. Invoice creation and upload retain compatibility with historical invoice records while also reserving new keys in the shared `idempotency_records` table. Migration `0012_unified_invoice_idempotency` backfills historical invoice keys; it deliberately stops with an explicit error if a historical key collides with a key already used by another operation, rather than silently changing either record.

## Idempotency-key retention

Idempotency responses are retained for 90 days by default. A tenant administrator can remove older records in bounded batches; cleanup is scoped to the authenticated tenant by both the query and PostgreSQL row-level security. The operation is audited. Use the same access token and repeat while `has_more` is true:

```http
POST /api/v1/maintenance/idempotency/cleanup?retention_days=90&batch_size=1000
Authorization: Bearer <tenant_admin_access_token>
```

The retention window must be between 30 and 3,650 days, and each batch is limited to 10,000 records. The endpoint only deletes idempotency response records; it does not delete invoices, supplier records, audit events, or uploaded files. After the retention window expires and records are cleaned up, an old key may be accepted as a new request, so clients should not retry business operations indefinitely. This endpoint is not a scheduler: run it periodically from an authenticated tenant maintenance process.

## Refresh-token lifecycle

Authentication responses return a short-lived access token and set a 30-day refresh credential in a host-only `HttpOnly` cookie (`ledgergate_refresh`). The cookie is scoped to `/api/v1/auth`, uses `SameSite=Lax`, and is marked `Secure` in production. The API also sets a separate readable `ledgergate_csrf` cookie. Browser clients must send its value in the `X-CSRF-Token` header for refresh and logout requests; the API also validates the request `Origin` against `CORS_ORIGINS`. Do not copy the refresh cookie into JavaScript storage. Successful refresh rotates the cookie automatically. Reuse of a previously rotated refresh token revokes the entire token family. `POST /api/v1/auth/logout` revokes the current refresh session and clears both cookies. Access tokens remain valid until their short expiry; logout does not immediately revoke already-issued access tokens. Use HTTPS in deployed environments. Since the CSRF cookie is host-only and read by the browser client, serve the frontend and API on the same hostname (a reverse proxy for `/api` is recommended); configure that exact frontend origin in `CORS_ORIGINS`.


### Log out all devices

Use the authenticated access token to revoke every unexpired refresh-token session for the current user in the current tenant. The endpoint also clears the current browser's refresh and CSRF cookies:

```http
POST /api/v1/auth/logout-all
Authorization: Bearer <access_token>
```

The endpoint returns the number of refresh sessions revoked. Existing access tokens remain valid until their short expiry; use short access-token lifetimes and refresh-token revocation together.


## Expired refresh-session retention

A tenant administrator can remove expired refresh-session rows after an investigation grace period. The default grace period is 30 days after token expiry; the endpoint never selects unexpired sessions, and deletion is tenant-scoped and audit logged:

```http
POST /api/v1/maintenance/auth-sessions/cleanup?retention_days=30&batch_size=1000
Authorization: Bearer <access_token>
```

`retention_days` is the number of days to retain a session after its `expires_at` timestamp (0–3,650). Batches are limited to 10,000 rows; repeat while `has_more` is true. Run this endpoint periodically from an authenticated tenant maintenance process. It is not a scheduler.


## Authenticator-app MFA

Users can enroll a TOTP authenticator after signing in. The setup response returns the shared secret and an `otpauth://` URI once; display it only to the account owner and never log it. The secret is encrypted before being stored. Enabling MFA also returns ten one-time 24-character recovery codes; store them securely because only their hashes are retained and the raw codes are shown once. Each code is single-use and can replace the authenticator code at login or when disabling MFA.

1. Start enrollment with the current password:

```http
POST /api/v1/auth/mfa/setup
Content-Type: application/x-www-form-urlencoded

current_password=<current-password>
```

2. Add the returned secret to an authenticator app, then confirm a current six-digit code:

```http
POST /api/v1/auth/mfa/enable
Content-Type: application/x-www-form-urlencoded

totp_code=<six-digit-code>
```

3. Include `totp_code` with the password at `POST /api/v1/auth/token` whenever MFA is enabled. A one-time `recovery_code` can be used instead if the authenticator is unavailable. To disable MFA, send `current_password` and either a fresh `totp_code` or an unused `recovery_code` to `POST /api/v1/auth/mfa/disable`. The authenticated status endpoint is `GET /api/v1/auth/mfa/status`.

TOTP uses 30-second SHA-1 time steps, accepts a one-step clock-skew window, and prevents reuse of a consumed time step. Failed MFA attempts count toward the existing login throttling. Store `MFA_ENCRYPTION_KEY` as a secret; generate it with `Fernet.generate_key()`. Losing or rotating this key without decrypt-and-reencrypt migration makes existing MFA secrets unreadable.


## Immutable signed audit export

Production must configure a dedicated `AUDIT_WORM_BUCKET` with S3 versioning enabled and S3 Object Lock enabled at bucket creation. Configure `AUDIT_EXPORT_SIGNING_KEY` with at least 32 random characters, identify its version using `AUDIT_EXPORT_SIGNING_KEY_ID`, and set `AUDIT_WORM_RETENTION_DAYS` (1–3,650; default 2,555 days). The application verifies bucket versioning and Object Lock configuration before each export and fails closed if either is unavailable.

An administrator exports a verified batch using:

```http
POST /api/v1/audit-events/export?after_id=0&batch_size=1000
Authorization: Bearer <admin-access-token>
```

The export contains the event IDs, canonical audit payloads, previous/event hashes, batch boundaries, SHA-256 digest, HMAC-SHA256 signature, and signing-key ID. The object key is deterministic for a given event range and payload digest. Objects are written with server-side encryption and S3 Object Lock `COMPLIANCE` retention. The response includes the exact S3 `version_id` and `next_after_id`; pass `next_after_id` as `after_id` to export the next batch. Keep the version ID with the export inventory so auditors can retrieve the precise locked object version. An export is refused if the tenant's full database hash chain does not verify. Each successful export is itself recorded in the database audit chain after the object write.

The bucket must be created and its IAM policy configured outside the application. Grant the API role `s3:GetBucketVersioning`, `s3:GetBucketObjectLockConfiguration`, `s3:PutObject`, and the provider's required object-retention permission; do not grant object deletion or retention-bypass permissions. Keep the signing key in a secrets manager and back it up separately. Rotating it requires a versioned key-management procedure so older exports remain verifiable.


## Tenant administrator: deactivate a workspace member

Only an administrator can deactivate a non-admin member in the same tenant. The endpoint refuses self-deactivation and refuses to deactivate another administrator. It marks the member inactive, revokes all of that member's active refresh sessions, and writes an audit event. The action is not a substitute for a full offboarding workflow (for example, reassignment of outstanding approvals or legal retention review).

```http
POST /api/v1/users/21/deactivate
Authorization: Bearer <tenant_admin_access_token>
```

Example response:

```json
{
  "status": "deactivated",
  "user_id": 21,
  "revoked_sessions": 2
}
```

An already inactive member returns `already_deactivated`. Deactivation is tenant-scoped; a user ID from another workspace returns HTTP 404. Deactivated accounts cannot sign in or refresh sessions.


## Tenant administrator: list workspace members

List only the authenticated administrator's tenant members. The response is paginated (maximum 200 per page) and deliberately excludes password hashes, MFA secrets, and recovery-code hashes:

```http
GET /api/v1/users?limit=100&offset=0
Authorization: Bearer <tenant_admin_access_token>
```

Each member entry includes ID, email, role, active status, MFA-enabled status, and creation time. Use the member ID with `POST /api/v1/users/{user_id}/deactivate` to deactivate a non-admin member.


### Change a workspace member's role

Only tenant administrators can change active, non-admin members between the analyst and reviewer roles. The operation revokes the member's active refresh sessions and records an audit event:

```http
PATCH /api/v1/users/21/role
Authorization: Bearer <tenant_admin_access_token>
Content-Type: application/json

{"role": "reviewer"}
```

A role change returns the new role and number of revoked sessions. Administrator roles cannot be changed through this endpoint; deactivated accounts must be reactivated through a separate, controlled lifecycle process before their role can be changed.


### View and revoke individual login sessions

A signed-in user can view their own active, unexpired refresh sessions. The response intentionally omits refresh tokens, token hashes, and session-family identifiers:

```http
GET /api/v1/auth/sessions
Authorization: Bearer <access_token>
```

To revoke one listed session:

```http
DELETE /api/v1/auth/sessions/19
Authorization: Bearer <access_token>
```

The session ID is scoped to the authenticated user's tenant and account. Revocation prevents further refresh-token use for that session; already-issued short-lived access tokens remain valid until they expire or the account is deactivated.


## Immutable audit export

A workspace administrator can export the next ascending batch of audit events to the configured S3-compatible Object Lock bucket. The API verifies the tenant's complete hash chain first and refuses the export with HTTP 409 if verification fails. Storage/configuration failures return HTTP 503; no local or non-immutable fallback is used.

```bash
curl -X POST 'http://localhost:8000/api/v1/audit-events/export?after_id=0&batch_size=1000' \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

For a successful export, persist the returned `next_after_id` and use it as `after_id` on the next request. `batch_size` is limited to 1–10,000. A response with `status: "no_events"` means there are no newer events at that cursor. Each successful batch records an `immutable_export_created` audit event containing the object key, version ID, event range, payload digest, Object Lock mode, and retention timestamp. Since that audit event is written after the exported batch, a later batch can include it. The dashboard's **Export next immutable batch** button starts at cursor zero for the current browser session and advances the cursor only after a successful export; retain the returned cursor externally if you need a durable multi-session export process.

This endpoint requires the production audit-export settings, a pre-created versioned Object Lock bucket with COMPLIANCE retention, and appropriately scoped storage credentials. The dashboard does not provision the bucket or keys. A successful API response proves the configured storage adapter accepted the object; separately verify bucket policy, retention, IAM, key custody, and retrieval in the target cloud account.
