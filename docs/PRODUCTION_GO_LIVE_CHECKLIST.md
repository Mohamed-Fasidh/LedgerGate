# Production go-live checklist

This checklist separates repository features from controls that must be configured and independently verified in the target environment. A green CI run is not a production approval.

## 1. Database and tenant isolation
- [ ] Replace the metadata-driven initial migration with a reviewed explicit baseline and document upgrade/rollback procedures.
- [ ] Provision separate migration-owner and runtime roles. The API role must not own tables, be superuser, or have BYPASSRLS.
- [ ] Design a dedicated pre-authentication data-access boundary for `users`, `login_throttles`, and `password_reset_tokens`; do not add a simplistic RLS policy that breaks login or reset flows.
- [ ] Remove broad authentication-table privileges from the runtime role only after registration, login, refresh, reset request, and reset confirmation pass integration tests with the final restricted credentials.
- [ ] Run cross-tenant read/write tests against every tenant-owned table and endpoint.
- [ ] Verify migrations against a copy of the actual target schema and data before release.

## 2. Secrets, network, and application
- [ ] Store JWT, database, SMTP, S3, and model-provider credentials in a managed secret store.
- [ ] Configure HTTPS ingress, HSTS, WAF, trusted proxy settings, strict CORS origins, and private database/object-storage networking.
- [ ] Confirm production environment rejects local storage, weak secrets, wildcard CORS, and insecure custom object-storage endpoints.
- [ ] Configure rate limits at the edge and application layers; test login, password-reset, upload, and API abuse scenarios.
- [ ] Review dependency vulnerabilities, CodeQL/SAST findings, container image vulnerabilities, and software supply-chain controls.

## 3. Document storage and malware scanning
- [ ] Complete a real cloud-bucket integration test using the production IAM/workload identity.
- [ ] Enforce bucket public-access block, encryption, TLS, tenant-scoped access, and short-lived downloads.
- [ ] Configure retention, lifecycle, versioning, deletion, legal hold where required, and storage monitoring.
- [ ] Verify ClamAV availability, signature updates, fail-closed behavior, file-size/page limits, and incident handling.
- [ ] Test backup and restore of both database metadata and stored document objects.

## 4. Identity and audit
- [ ] Configure SMTP sender/domain authentication and a hosted HTTPS password-reset page.
- [ ] Decide whether SSO/OIDC, email verification, device notifications, and enterprise identity lifecycle are required for launch.
- [ ] Configure external append-only/WORM audit replication and independent hash checkpoints.
- [ ] Test audit verification, alerting, retention, and incident evidence export.

## 5. Financial workflow controls
- [ ] Configure approval policies separately for each supported currency; never infer an FX rate or compare thresholds across currencies without an approved rate source and policy.
- [ ] Validate maker-checker, distinct reviewer identities, policy snapshots, rejection, and race/concurrency behavior.
- [ ] Review idempotency coverage for every externally retried mutation.
- [ ] Confirm the product remains an invoice verification/approval workflow and does not initiate payments unless a separately designed and audited payment integration is approved.

## 6. Operational readiness
- [ ] Deploy to a staging environment matching production topology and run end-to-end smoke tests.
- [ ] Run representative load, soak, and failure/recovery tests.
- [ ] Configure metrics, logs, traces, dashboards, paging, and service-level objectives.
- [ ] Enable database backups/PITR and complete a witnessed restore drill.
- [ ] Exercise incident response, credential rotation, tenant offboarding, data export/deletion, and disaster recovery.
- [ ] Complete privacy, legal, regulatory, and independent security reviews for the launch jurisdictions.
- [ ] Obtain written sign-off from engineering, security, operations, and the business owner.

## Release decision
Do not process real customer financial records until all applicable items are evidenced, reviewed, and signed off. Repository CI success alone is insufficient.
