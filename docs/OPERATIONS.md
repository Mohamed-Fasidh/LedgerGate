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

## Database backup and restore

### Local Docker backup
For local development, create a PostgreSQL custom-format backup. Keep the output outside the container:
```bash
docker compose exec -T db pg_dump -U ledgergate -d ledgergate -Fc > ledgergate-backup.dump
```
Check that the dump is readable:
```bash
pg_restore --list ledgergate-backup.dump
```

### Production backup requirements
- Use the managed PostgreSQL provider's automated backups and point-in-time recovery (PITR); set and document an explicit recovery point objective (RPO) and recovery time objective (RTO).
- Enable provider-side encryption for backups and snapshots, restrict backup access to designated operators, and keep backup retention aligned with legal and contractual requirements.
- Store backup copies separately from the primary database account/project where supported. Do not commit backups, credentials, connection strings, or encryption keys to this repository.
- Schedule backups and alert on missed or failed backup jobs. A successful backup job is not proof that a database can be restored.
- Before a release that changes schema or data, take a verified backup or confirm a recent provider snapshot and document the recovery owner.

### Restore verification
Perform restore drills in an isolated environment, never over the live production database:
```bash
createdb ledgergate_restore_check
pg_restore --exit-on-error --no-owner --dbname=ledgergate_restore_check ledgergate-backup.dump
```
After restore, run Alembic migration-state checks and application smoke tests, verify representative tenant-scoped records and audit-chain integrity, and record the restore duration and data-loss window. For managed PITR, periodically restore to a new instance and run the same checks. Restrict the restored instance and securely delete it after the drill.

The commands above assume PostgreSQL client tools are installed and that authentication is supplied securely through the environment or a protected PostgreSQL password file; do not put passwords directly in shell history. A restore drill in a separate environment is required before relying on backups.

## Incident response
1. Disable affected user accounts and rotate credentials.
2. Preserve logs and audit events.
3. Identify affected tenant and record IDs.
4. Restore from a known-good backup if integrity is compromised.
5. Notify security/privacy owners and follow applicable legal obligations.
6. Document root cause and corrective actions.
