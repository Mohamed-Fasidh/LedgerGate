# Deployment guide

## 1. Local development

1. Copy `.env.example` to `.env`; replace every sample secret with a unique local value.
2. Ensure Docker Desktop/Engine is running.
3. Start the stack with `docker compose up --build -d`.
4. Follow startup with `docker compose logs -f api`.
5. Verify API health at `http://localhost:8000/health`, API docs at `http://localhost:8000/docs`, and the dashboard at `http://localhost:3000`.
6. Register a test workspace and exercise supplier, PO, invoice upload, extraction confirmation, reconciliation, approval, audit verification, and sign-out flows.
7. Stop with `docker compose down`. Use `docker compose down -v` only when you intentionally want to delete local database and upload volumes.

Never use sample passwords or secrets outside a disposable local environment.

## 2. Recommended production topology

Internet → managed WAF/load balancer with TLS → one public hostname → Next.js frontend and FastAPI API (reverse-proxy `/api`) → private managed PostgreSQL and private object storage.

Keep PostgreSQL, ClamAV, and model servers on private networks. Do not expose database or scanner ports publicly. Use workload identity/IAM roles instead of long-lived cloud access keys wherever possible.

The frontend and API must be served on the **same hostname** for the host-only CSRF cookie design. Configure the frontend API base URL to a same-origin path such as `/api/v1`, and proxy that path to FastAPI. Do not deploy the UI and API on unrelated hostnames without redesigning and retesting the cookie/CSRF model.

## 3. Required environment configuration

Configure all values through the cloud secret manager or protected deployment environment, not source control:

- `APP_ENV=production`
- Strong, unique `JWT_SECRET_KEY` (at least 32 characters of cryptographically random material).
- Strong, separate `POSTGRES_PASSWORD` and `DB_RUNTIME_PASSWORD`.
- `MIGRATION_DATABASE_URL` for the migration owner; `DATABASE_URL` for the restricted `ledgergate_runtime` role.
- Production `DATABASE_URL` must use `sslmode=verify-full` and an explicit mounted `sslrootcert` CA bundle; do not downgrade to `require`, `prefer`, or plaintext. Mount the CA certificate read-only and include its path in the connection URL.
- Exact `CORS_ORIGINS` containing public UI origins only (for example `https://app.example.com`); do not include paths or trailing slashes because browser `Origin` headers contain only the origin.
- `PUBLIC_APP_URL` must be the public HTTPS frontend origin only, with no path, query, fragment, credentials, or localhost hostname.
- `STORAGE_BACKEND=s3`, `S3_BUCKET`, `S3_REGION`, and workload IAM credentials/identity.
- Private ClamAV endpoint, signature update process, and scanner resource limits.
- Valid `MFA_ENCRYPTION_KEY` (Fernet key).
- `AUDIT_WORM_BUCKET` with S3 versioning and Object Lock enabled.
- Unique `AUDIT_EXPORT_SIGNING_KEY` and `AUDIT_EXPORT_SIGNING_KEY_ID`, protected and rotated under a documented key-custody process.
- Approved `AUDIT_WORM_RETENTION_DAYS` within the supported 1–3650 day range.
- Authenticated SMTP account-recovery delivery: `EMAIL_SMTP_HOST`, `EMAIL_SMTP_PORT=587`, `EMAIL_SMTP_USERNAME`, `EMAIL_SMTP_PASSWORD`, and `EMAIL_FROM_ADDRESS`.
- `PUBLIC_APP_URL` set to the HTTPS public application origin used to construct password-reset and email-verification links.
- Dedicated `EMAIL_OUTBOX_ENCRYPTION_KEY` (valid Fernet key) for encrypting bearer-token URLs stored in the durable email outbox. Generate it with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and store it in the secret manager; do not reuse the JWT or MFA key.

New registrations are created as unverified and cannot sign in until the email-verification link is confirmed. Existing accounts are preserved as verified by migration default. Account-security messages are inserted into `outbound_emails` in the same transaction as the verification/reset token; encrypted payloads are delivered by the separate `email-worker` Compose service. The worker retries transient failures with exponential backoff (30 seconds up to one hour), reclaims abandoned five-minute leases, and moves exhausted messages to `dead`. Provide a real SMTP provider and validate both verification and reset messages in staging before launch.

Production startup deliberately fails if required MFA/outbox encryption, audit WORM, SMTP account-recovery, HTTPS public-app URL, or JWT settings are absent/invalid. Do not disable these checks to make a deployment start.

## 4. Database release process

- Provision managed PostgreSQL with private networking, encryption, backups, and point-in-time recovery.
- Run `sh ./scripts/bootstrap_auth_owner_role.sh` once before migrations using a privileged database bootstrap connection. It provisions `ledgergate_auth_owner` as a NOLOGIN/BYPASSRLS role, grants it only the schema privileges needed to own fixed authentication lookup functions, and grants membership to the migration principal so it can `SET ROLE` while creating those functions. The role must never be granted to the application runtime role.
- Run Alembic migrations once as a controlled release job using the migration-owner connection. Revision `0027_tenant_owned_references` preflights existing purchase-order, invoice, approval, session, and auth-token references and aborts if it finds missing or cross-tenant parents. Reconcile any flagged records explicitly before retrying; do not bypass the preflight or rewrite the frozen `0001_initial.py` baseline. After migration, verify the composite tenant-aware foreign keys and same-tenant triggers are present.
- Run `./scripts/bootstrap_runtime_role.sh` as a separate one-shot release/bootstrap task using the migration-owner connection, then verify the runtime role grants. The script removes blanket grants, grants only the reviewed current application-table allowlist (with read-only access to `alembic_version`), grants usage on existing application sequences, and revokes default table/sequence privileges for the migration owner.
- Every future migration that creates a table or sequence used by the API or email worker must add an explicit runtime grant and a privilege regression test. New objects intentionally receive no implicit runtime access.
- The production API image starts only Uvicorn; it does not run migrations or create database roles on startup. This prevents every replica from racing to perform privileged schema changes. The repository's local `docker-compose.yml` deliberately overrides the command to run migrations and runtime-role bootstrap for developer convenience only; do not copy that override into a production deployment.
- Configure the orchestrator's API readiness gate to wait for the release migration/bootstrap job to succeed before starting or rolling out API replicas. The API role must not own tenant tables, be a superuser, or have BYPASSRLS.
- Run RLS and cross-tenant negative tests as the exact runtime role.
- Start API replicas only after the migration job succeeds. Do not run migrations concurrently from every API replica.
- Validate tenant context is reset between pooled requests and that no tenant can access another tenant's records.

## 5. Invoice storage and malware scanning

The local Compose stack runs ClamAV and sends uploaded bytes through INSTREAM before storage. Scanner unavailability fails closed; malware is rejected.

For production:
- Use a private, versioned bucket with public access blocked.
- Grant the API identity only the minimum required object permissions for its application prefix.
- Enforce TLS-only bucket access, encryption at rest, access logging, and lifecycle/retention rules.
- Current adapters request AES256/SSE-S3. If customer-managed KMS encryption is required, implement and test SSE-KMS with an explicit key ID and least-privilege KMS grants before claiming that control.
- Configure ClamAV signature updates, health monitoring, CPU/memory limits, and private-only network access.
- Test object restore, malware detection, scanner outage, and tenant-authorized download paths. Document extraction caps text at 2 million characters, PDFs at 50 pages, and OCR rendering at 12 million pixels per page / 48 million pixels per document before allocating page images.

No cloud bucket, IAM role, KMS key, DNS name, or production credential is provisioned by this repository.

## 6. Immutable audit export

The application can verify the tenant audit hash chain and write signed export batches to an S3 Object Lock bucket. Before enabling it:
- Create a dedicated bucket with versioning and Object Lock enabled at creation time.
- Approve retention mode/duration and ensure the bucket is administered separately from the application role.
- Store the HMAC signing key in a managed secret/KMS-backed custody process; document key ID, access, backup, rotation, and compromise response.
- Grant only the required bucket/object permissions and test configuration discovery, write, version ID capture, retention, signature verification, and failure handling.
- Run a restore/independent verification drill and alert on failed exports or invalid audit chains.

The application code does not create the bucket, establish key custody, or independently prove the cloud administrator's controls.

## 7. Container runtime hardening

- The API image runs as dedicated UID/GID 10001; its upload directory is created with matching ownership so the local named volume can be initialized for the unprivileged process.
- The frontend image runs as the unprivileged `node` user.
- Local Compose applies `no-new-privileges` and drops all Linux capabilities for the API and frontend containers; neither service needs privileged port binding. Revalidate these restrictions against any added native dependencies or runtime features.
- The API exposes `/health` as a lightweight process liveness endpoint and `/ready` as a dependency-aware readiness endpoint. `/ready` executes `SELECT 1` against PostgreSQL and checks the configured ClamAV TCP endpoint; it returns HTTP 503 if either required dependency is unavailable. In development only, an unconfigured scanner is reported as `not_configured`; production configuration requires ClamAV. The probe verifies scanner reachability, not signature freshness or a complete malware scan.
- Local Compose uses `/ready` for the API health check and gates frontend startup on API readiness. The frontend has its own HTTP health check. S3 permissions and object operations still require staging integration tests; readiness does not perform a bucket write.
- Keep container images pinned to reviewed base-image digests for controlled production releases, and apply resource limits/read-only root filesystems where compatible with the upload and temporary-file paths.

## 8. Edge security and operations

- TLS everywhere; HSTS and secure response headers at the edge.
- WAF, request/body limits, rate limiting, DDoS protection, and trusted proxy configuration.
- Managed secrets, key rotation, private service networking, and least-privilege IAM.
- Centralized structured logs, metrics, traces, dashboards, and actionable alerts.
- Health/readiness probes, resource limits, autoscaling policy, and documented rollback procedure.
- Database backups/PITR and periodic restore drills; object versioning/retention and recovery tests.
- Incident response contacts, escalation, audit retention policy, privacy/legal review, and operational ownership.

## 9. Pre-production acceptance checklist

- [ ] Current PR changes reviewed and merged; default branch CI green.
- [ ] Staging environment provisioned and deployed from a pinned commit/image digest.
- [ ] All migrations applied once; runtime role and FORCE RLS verified.
- [ ] Cross-tenant authorization and negative tests pass.
- [ ] Login, refresh rotation/reuse detection, logout, logout-all, CSRF, MFA, and recovery flows tested.
- [ ] Invoice upload, scanner fail-closed behavior, extraction review, reconciliation, maker-checker, and multi-stage approval tested.
- [ ] Idempotency replay and mismatched-key conflict tests pass for every supported operation.
- [ ] Audit chain verification and S3 Object Lock signed export verified end-to-end.
- [ ] TLS, WAF, secrets, IAM, monitoring, alerting, backup and restore configured.
- [ ] Dependency/security scans, independent penetration test, load test, and privacy/legal review completed.
- [ ] UAT, rollback, incident-response and disaster-recovery drills signed off.

A successful GitHub Actions build is necessary but not sufficient for production approval. LedgerGate is an invoice verification and human-approval platform; it does not initiate payments or autonomously approve financial transactions.
