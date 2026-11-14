"""Inventory document objects and optionally remove true orphans.

Run as a scheduled maintenance job with a database role that can read all
invoice rows. It is dry-run by default and requires two explicit flags before
deleting anything.
"""
import argparse

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Invoice
from app.services.storage import delete_document, list_document_keys


def referenced_document_keys(session) -> set[str]:
    return set(session.scalars(select(Invoice.file_path).where(Invoice.file_path.is_not(None))).all())


def reconcile(*, delete: bool = False) -> tuple[set[str], set[str], set[str]]:
    """Return (orphans, missing_objects, deleted)."""
    with SessionLocal() as session:
        referenced = referenced_document_keys(session)
    stored = list_document_keys()
    orphans = stored - referenced
    missing = referenced - stored
    deleted = set()
    if delete:
        for key in sorted(orphans):
            delete_document(key, key.split("/", 1)[0])
            deleted.add(key)
    return orphans, missing, deleted


def main() -> int:
    parser = argparse.ArgumentParser(description="Reconcile LedgerGate document storage against invoice records")
    parser.add_argument("--delete-orphans", action="store_true")
    parser.add_argument("--confirm-delete", action="store_true")
    args = parser.parse_args()
    if args.delete_orphans and not args.confirm_delete:
        parser.error("--delete-orphans requires --confirm-delete")
    orphans, missing, deleted = reconcile(delete=args.delete_orphans)
    print(f"orphan_objects={len(orphans)} missing_referenced_objects={len(missing)} deleted={len(deleted)}")
    for key in sorted(orphans):
        print(f"orphan {key}")
    for key in sorted(missing):
        print(f"missing {key}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
