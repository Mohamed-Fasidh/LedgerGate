# Deployment guide

## Local Docker deployment
1. Copy `.env.example` to `.env`.
2. Replace the local database password and JWT secret. Never use the sample values outside local development.
3. Run `docker compose up --build`.
4. Compose waits for PostgreSQL, runs the one-shot Alembic migration service, then starts the API and web services.
5. Confirm `/health`, `/docs`, and the dashboard.

## Render and Vercel deployment notes
- Deploy the FastAPI service from `backend/` and configure its start command as `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Set the working directory to `backend`.
- Set `APP_ENV=production`, `DATABASE_URL` to a managed PostgreSQL URL using psycopg, a unique `JWT_SECRET_KEY` of at least 32 bytes, and `CORS_ORIGINS` to the exact HTTPS Vercel origin(s).
- Set `MAX_UPLOAD_MB` to a conservative limit. The current upload implementation writes to local disk. Do not enable production invoice uploads on an ephemeral filesystem; a private encrypted object-storage adapter, malware scanning, and access-controlled retrieval must be implemented first or a durable encrypted mounted volume must be provisioned with an explicit retention plan.
- Run `alembic upgrade head` as a controlled pre-deploy/release command from the backend working directory. Do not run migrations independently in every API replica.
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
