# Deployment guide

## Local Docker deployment
1. Copy `.env.example` to `.env`.
2. Replace the local database password and JWT secret. Never use the sample values outside local development.
3. Run `docker compose up --build`.
4. Compose waits for PostgreSQL, runs the one-shot Alembic migration service, then starts the API and web services.
5. Confirm `/health`, `/docs`, and the dashboard.
6. Compose initializes a separate `ledgergate_runtime` role for the API; migrations use the database bootstrap account. The runtime role is non-owner and `NOBYPASSRLS`.

The PostgreSQL init script runs only when the data volume is first created. If you already have a `ledgergate_pgdata` volume, create the runtime role and grants manually using `docker/postgres/01-create-runtime-role.sh` logic, or back up your data and recreate the volume for a clean local development database. Do not delete a volume containing data you need.

## Render and Vercel deployment notes
- Deploy the FastAPI service from `backend/` and configure its start command as `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Set the working directory to `backend`.
- Set `APP_ENV=production`, `DATABASE_URL` to a managed PostgreSQL URL using psycopg, a unique `JWT_SECRET_KEY` of at least 32 bytes, and `CORS_ORIGINS` to the exact HTTPS Vercel origin(s).
- Set `STORAGE_BACKEND=s3`, `S3_BUCKET`, and `S3_REGION`; configure a workload identity or both static S3 credentials. Production startup rejects local storage and non-HTTPS custom S3 endpoints. Follow [Private document storage](OBJECT_STORAGE.md) to configure bucket privacy, IAM, TLS, encryption, lifecycle, and monitoring. The code requests SSE-S3 AES-256 and uses tenant-scoped object keys with short-lived presigned downloads. A real cloud-bucket integration and policy review are still required.
- Run `alembic upgrade head` as a controlled pre-deploy/release command from the backend working directory. Do not run migrations independently in every API replica.

### PostgreSQL tenant RLS role separation

The `0002_tenant_rls` migration enables and forces row-level security on `suppliers`, `purchase_orders`, `invoices`, `policy_documents`, `agent_memories`, and `audit_events`. The API sets the transaction-local PostgreSQL setting `app.tenant_id` only after authenticating the user; it is reapplied automatically at each new SQLAlchemy transaction. A missing tenant context matches no rows.

**Do not run the API as a PostgreSQL superuser, a role with `BYPASSRLS`, or the table owner.** Use a migration/owner role for the controlled release command and a separate non-owner runtime role for the API. Example (run as your database administrator; replace both passwords):

```sql
CREATE ROLE ledgergate_migrator LOGIN PASSWORD 'REPLACE_WITH_MIGRATOR_SECRET' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE ledgergate_runtime LOGIN PASSWORD 'REPLACE_WITH_RUNTIME_SECRET' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;

GRANT CONNECT ON DATABASE ledgergate TO ledgergate_runtime;
GRANT USAGE ON SCHEMA public TO ledgergate_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ledgergate_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ledgergate_runtime;

ALTER DEFAULT PRIVILEGES FOR ROLE ledgergate_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ledgergate_runtime;
ALTER DEFAULT PRIVILEGES FOR ROLE ledgergate_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO ledgergate_runtime;
```

Create/own the application schema objects with `ledgergate_migrator`, run migrations using its connection string, and configure the API's `DATABASE_URL` with the `ledgergate_runtime` credentials. Ensure the runtime role is not a member of the migrator role and has no `BYPASSRLS` attribute. If your managed database provider restricts role creation or grants, follow its supported least-privilege procedure before deploying.

The `users` table is intentionally not covered by these tenant-data policies because login must locate a user before a tenant can be established. User lookup is constrained by the authenticated token's user ID and tenant ID; this table still needs a separate identity-store hardening review.
- Deploy the frontend from `frontend/`. Configure `NEXT_PUBLIC_API_URL` to the public API base URL ending in `/api/v1` and `NEXT_PUBLIC_API_DOCS_URL` to the public API docs URL. These are Next.js public build-time variables; set them before the frontend build.
- Configure the backend CORS allowlist to include the exact Vercel production domain and any required preview domains. Avoid broad wildcard preview origins.
- Configure health check path `/health` for the API service. Keep the database private and restrict network access to the API service.

## Production topology recommendation
Internet → managed WAF/load balancer/TLS → frontend and API services → private PostgreSQL and private object storage. Keep database and model servers on private networks. Run background extraction/evaluation workers separately when introduced.

## Production gates
- The initial migration currently creates tables from SQLAlchemy metadata; replace it with a reviewed, explicit baseline migration before adopting an existing production database or relying on schema drift detection.
- Configure PostgreSQL RLS with FORCE ROW LEVEL SECURITY, a non-owner runtime DB role, and cross-tenant integration tests.
- Add SSO/OIDC, MFA, login throttling, password reset, refresh-token rotation/revocation, and account recovery.
- Use private encrypted object storage, malware scanning, content sniffing, retention/deletion, and signed download URLs.
- Use append-only/WORM audit storage, idempotency keys, separation of duties, dual approval thresholds, and optimistic locking.
- Use managed secrets, TLS, rate limiting, WAF, backups/PITR, and tested restore procedures.
- Run dependency scanning, SAST/DAST, penetration tests, load tests, privacy/legal review, and incident response exercises.

## CI
GitHub Actions runs Python compilation and pytest, plus a Next.js production build, on pushes to `main` and `production-readiness` and on pull requests targeting `main`. A green CI run is necessary but not sufficient for production approval.
