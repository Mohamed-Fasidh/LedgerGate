from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.services import storage
from scripts import reconcile_storage


def test_reconciliation_is_dry_run_by_default_and_deletes_only_old_orphans(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    old = str(tmp_path / "tenant-a" / (("a" * 32) + ".pdf"))
    recent = str(tmp_path / "tenant-a" / (("b" * 32) + ".txt"))
    referenced = str(tmp_path / "tenant-b" / (("c" * 32) + ".pdf"))
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(reconcile_storage, "_read_referenced_locations", lambda: {referenced})
    monkeypatch.setattr(reconcile_storage, "list_invoice_objects", lambda: {
        old: now - timedelta(hours=30),
        recent: now - timedelta(hours=2),
    })
    deleted = []
    monkeypatch.setattr(reconcile_storage, "delete_invoice", lambda tenant, location: deleted.append((tenant, location)))

    orphans, missing, removed, deferred = reconcile_storage.reconcile()

    assert orphans == {old, recent}
    assert missing == {referenced}
    assert removed == set()
    assert deferred == {recent}
    assert deleted == []

    orphans, missing, removed, deferred = reconcile_storage.reconcile(delete=True)

    assert removed == {old}
    assert deferred == {recent}
    assert deleted == [("tenant-a", old)]


def test_reconciliation_requires_a_minimum_24_hour_grace_period():
    with pytest.raises(ValueError, match="at least 24"):
        reconcile_storage.reconcile(minimum_age_hours=23)


def test_tenant_for_location_validates_s3_object_key(monkeypatch):
    monkeypatch.setattr(settings, "s3_bucket", "ledgergate-private")
    location = "s3://ledgergate-private/tenants/tenant-1/invoices/" + "d" * 32 + ".pdf"

    assert reconcile_storage._tenant_for_location(location) == "tenant-1"

    with pytest.raises(ValueError):
        reconcile_storage._tenant_for_location("s3://ledgergate-private/tenants/tenant-2/../secret.txt")


def test_tenant_for_location_rejects_local_path_outside_storage_root(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    outside = tmp_path.parent / ("x" * 32 + ".pdf")

    with pytest.raises(ValueError, match="outside"):
        reconcile_storage._tenant_for_location(str(outside))


def test_database_snapshot_requires_a_dedicated_maintenance_url(monkeypatch):
    monkeypatch.delenv("RECONCILIATION_DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="RECONCILIATION_DATABASE_URL"):
        reconcile_storage._read_referenced_locations()
