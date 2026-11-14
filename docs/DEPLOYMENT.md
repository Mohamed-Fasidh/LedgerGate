# Deployment guide

## Local Docker deployment
- Configure `.env`.
- Use unique random secrets and a strong database password.
- Run `docker compose up --build`.
- Confirm `/health`, `/docs`, and the dashboard.

## Production topology recommendation
Internet → managed WAF/load balancer/TLS → frontend and API services → private PostgreSQL and object storage. Keep database and model servers on private networks. Run background extraction/evaluation workers separately when introduced.

## Production gates
- Migrate database in a controlled release job, not concurrently from every replica.
- Use managed secrets and non-root containers.
- Set resource limits, health checks, autoscaling policy, and log/metric export.
- Use a private bucket with encryption and malware scanning.
- Configure DB backup/PITR and test restore.
- Set exact CORS origins and trusted proxy configuration.
- Run dependency, security, and load tests before traffic.
