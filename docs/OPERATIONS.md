# Operations runbook

## Health checks
- API: `GET /health`
- API schema: `/docs`
- Database: Docker health check `pg_isready`
- Frontend: `http://localhost:3000`

## Common commands
```bash
docker compose ps
docker compose logs -f api
docker compose logs -f db
docker compose down
docker compose up --build -d
```

## Database backup example
For local Docker development:
```bash
docker compose exec -T db pg_dump -U ledgergate ledgergate > ledgergate-backup.sql
```
Restore drills must be performed in a separate environment before relying on backups.

## Incident response
1. Disable affected user accounts and rotate credentials.
2. Preserve logs and audit events.
3. Identify affected tenant and record IDs.
4. Restore from a known-good backup if integrity is compromised.
5. Notify security/privacy owners and follow applicable legal obligations.
6. Document root cause and corrective actions.
