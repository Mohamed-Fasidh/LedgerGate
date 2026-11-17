"""Safely reconcile invoice database references with private object storage.

Dry-run is the default. Deletion requires two explicit flags and only applies
to generated objects older than the mandatory 24-hour grace period. The
database connection must be a non-superuser BYPASSRLS maintenance role and is
forced read-only; never use the application runtime credential here.
"""
import argparse
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import create_engine, select, text

from app.models import Invoice
from app.services.storage import (
    INVOICE_OBJECT_PATTERN,
    TENANT_ID_PATTERN,
    delete_invoice,
    list_invoice_objects,
)
from app.config import settings


MINIMUM_DELETE_AGE_HOURS = 24


def _read_referenced_locations() -> set[str]:
    url = os.environ.get("RECONCILIATION_DATABASE_URL", "").strip()
    if not url:
        raise RuntimeError("RECONCILIATION_DATABASE_URL must be set to a read-only maintenance connection")

    engine = create_engine(url, pool_pre_ping=True)
    try:
        if engine.dialect.name != "postgresql":
            raise RuntimeError("Storage reconciliation requires PostgreSQL")
        with engine.connect() as connection:
            with connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                role = connection.execute(text("""
                    SELECT current_user, rolbypassrls, rolsuper
                    FROM pg_roles WHERE rolname = current_user
                """)).one_or_none()
                if (
                    role is None
                    or role[0] == "ledgergate_runtime"
                    or not role[1]
                    or role[2]
                ):
                    raise RuntimeError(
                        "RECONCILIATION_DATABASE_URL must use a non-superuser BYPASSRLS maintenance role"
                    )
                return {
                    value for value in connection.execute(
                        select(Invoice.file_path).where(Invoice.file_path.is_not(None))
                    ).scalars().all()
                    if isinstance(value, str)
                }
    finally:
        engine.dispose()


def _tenant_for_location(location: str) -> str:
    if not isinstance(location, str) or not location:
        raise ValueError("Invalid invoice storage location")
    if location.startswith("s3://"):
        parsed = urlparse(location)
        if (
            parsed.scheme != "s3"
            or parsed.netloc != settings.s3_bucket
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or "%" in parsed.path or "\\" in location
        ):
            raise ValueError("Invalid S3 invoice location")
        key = parsed.path.lstrip("/")
        match = INVOICE_OBJECT_PATTERN.fullmatch(key)
        if match is None:
            raise ValueError("Invalid S3 invoice object key")
        return match.group("tenant")

    root = Path(settings.upload_dir).resolve()
    path = Path(location).resolve(strict=False)
    try:
        relative_parent = path.parent.relative_to(root)
    except ValueError as exc:
        raise ValueError("Local invoice location is outside the configured storage root") from exc
    if len(relative_parent.parts) != 1 or TENANT_ID_PATTERN.fullmatch(relative_parent.parts[0]) is None:
        raise ValueError("Invalid local invoice tenant directory")
    if re.fullmatch(r"[0-9a-f]{32}\.(?:pdf|txt)", path.name) is None:
        raise ValueError("Invalid local invoice object name")
    return relative_parent.parts[0]


def reconcile(*, delete: bool = False, minimum_age_hours: int = MINIMUM_DELETE_AGE_HOURS):
    """Return orphaned, missing, deleted, and too-recent object locations."""
    if isinstance(minimum_age_hours, bool) or minimum_age_hours < MINIMUM_DELETE_AGE_HOURS:
        raise ValueError(f"minimum_age_hours must be at least {MINIMUM_DELETE_AGE_HOURS}")
    referenced = _read_referenced_locations()
    stored = list_invoice_objects()
    orphans = set(stored) - referenced
    missing = referenced - set(stored)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=minimum_age_hours)
    eligible = {
        location for location in orphans
        if stored[location].astimezone(timezone.utc) <= cutoff
    }
    recent = orphans - eligible
    deleted = set()
    if delete:
        for location in sorted(eligible):
            delete_invoice(_tenant_for_location(location), location)
            deleted.add(location)
    return orphans, missing, deleted, recent


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile LedgerGate invoice references and private object storage"
    )
    parser.add_argument("--delete-orphans", action="store_true")
    parser.add_argument("--confirm-delete", action="store_true")
    parser.add_argument("--minimum-age-hours", type=int, default=MINIMUM_DELETE_AGE_HOURS)
    args = parser.parse_args()
    if args.delete_orphans and not args.confirm_delete:
        parser.error("--delete-orphans requires --confirm-delete")
    try:
        orphans, missing, deleted, recent = reconcile(
            delete=args.delete_orphans,
            minimum_age_hours=args.minimum_age_hours,
        )
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(
        f"orphan_objects={len(orphans)} missing_referenced_objects={len(missing)} "
        f"deleted={len(deleted)} deferred_recent={len(recent)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
