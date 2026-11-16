# Deployment guide

## Local Docker deployment
- Configure `.env`.
- Use unique random secrets and a strong database password.
- Run `docker compose up --build`.
- Confirm `/health`, `/docs`, and the dashboard.

## Production topology recommendation
Internet → managed WAF/load balancer/TLS → frontend and API services → private PostgreSQL and object storage. Keep database and model servers on private networks. Run background extraction/evaluation workers separately when introduced.

## Production gates
- Migrate database in a controlled release job, not concurrently from every replica. The included single-container Compose command migrates as the owner, bootstraps `ledgergate_runtime`, then starts the API with the non-owner role. For multi-replica production, split migration/bootstrap into a one-shot release job and run API replicas with only `DATABASE_URL` for `ledgergate_runtime`.
- Use managed secrets and non-root containers. Set distinct `POSTGRES_PASSWORD` (migration owner) and `DB_RUNTIME_PASSWORD` (API runtime) values; never use the sample values in production.
- Set resource limits, health checks, autoscaling policy, and log/metric export.
- Use a private bucket with encryption and malware scanning.
- Configure DB backup/PITR and test restore.
- Set exact CORS origins and trusted proxy configuration.
- Run dependency, security, and load tests before traffic.

## Invoice storage and malware scanning
The local Compose profile runs a ClamAV daemon and sends uploaded invoice bytes through its INSTREAM protocol before storage. If the scanner is unavailable, uploads fail closed (HTTP 503). A positive malware result is rejected (HTTP 415).

For production, set:
- `APP_ENV=production`
- `STORAGE_BACKEND=s3`, `S3_BUCKET`, and `S3_REGION`
- Optional `S3_ENDPOINT_URL` for an S3-compatible private service
- Workload identity / IAM role where possible; otherwise inject `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY` from a secrets manager
- `CLAMAV_HOST` reachable only on the private application network

Grant the API identity only `s3:PutObject` and `s3:GetObject` for the dedicated bucket/prefix. Enable bucket public-access blocking, bucket versioning, lifecycle/retention policies, server-side encryption (prefer a customer-managed KMS key), TLS-only bucket policy, and access logging. The current adapter requests SSE-S3; change it to SSE-KMS and pass a key ID before claiming customer-managed encryption. Do not make the bucket public. Configure the ClamAV service with current signatures and resource limits. Scanner failure rejects the upload.

Local filesystem mode is development-only and production startup refuses it. No cloud bucket or credentials are provisioned by this repository.

## Database runtime role
The API process is configured with a dedicated `ledgergate_runtime` role (`NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`, `NOINHERIT`, `NOBYPASSRLS`). Migrations use `MIGRATION_DATABASE_URL`; the API uses `DATABASE_URL`. The bootstrap script grants existing and future table/sequence access to the runtime role. Review the generated grants and reduce them further for your deployment's least-privilege requirements.
