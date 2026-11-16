import json
from datetime import datetime, timezone

import pytest

from app.config import settings
from app.services import audit_export


class FakeS3:
    def __init__(self, versioning=None, lock=None):
        self.versioning = versioning or {"Status": "Enabled"}
        self.lock = lock or {"ObjectLockConfiguration": {"ObjectLockEnabled": "Enabled"}}
        self.put_args = None

    def get_bucket_versioning(self, **kwargs):
        return self.versioning

    def get_object_lock_configuration(self, **kwargs):
        return self.lock

    def put_object(self, **kwargs):
        self.put_args = kwargs
        return {"VersionId": "version-123"}


def _events():
    return [{
        "id": 1,
        "tenant_id": "tenant-a",
        "actor_user_id": 7,
        "entity_type": "invoice",
        "entity_id": "99",
        "action": "created",
        "details": {"amount": "12.00"},
        "created_at": datetime(2026, 11, 16, tzinfo=timezone.utc).isoformat(),
        "previous_hash": "0" * 64,
        "event_hash": "a" * 64,
    }]


def _settings(monkeypatch):
    monkeypatch.setattr(settings, "audit_worm_bucket", "ledgergate-audit-worm")
    monkeypatch.setattr(settings, "audit_export_signing_key", "test-signing-key-with-more-than-32-bytes")
    monkeypatch.setattr(settings, "audit_export_signing_key_id", "test-key-v1")
    monkeypatch.setattr(settings, "audit_worm_retention_days", 365)


def test_audit_export_is_signed_and_written_in_compliance_mode(monkeypatch):
    _settings(monkeypatch)
    client = FakeS3()
    monkeypatch.setattr(audit_export, "_s3_client", lambda: client)

    result = audit_export.store_immutable_audit_export("tenant-a", _events())
    args = client.put_args
    document = json.loads(args["Body"])

    assert result["object_lock_mode"] == "COMPLIANCE"
    assert result["version_id"] == "version-123"
    assert args["ObjectLockMode"] == "COMPLIANCE"
    assert args["ServerSideEncryption"] == "AES256"
    assert args["Bucket"] == "ledgergate-audit-worm"
    assert args["ObjectLockRetainUntilDate"] > datetime.now(timezone.utc)
    assert document["event_count"] == 1
    assert document["signing_key_id"] == settings.audit_export_signing_key_id
    assert audit_export.verify_audit_export_signature(document, settings.audit_export_signing_key)
    tampered_algorithm = dict(document, signature_algorithm="MD5")
    assert not audit_export.verify_audit_export_signature(tampered_algorithm, settings.audit_export_signing_key)
    assert not audit_export.verify_audit_export_signature(document, "different-signing-key-which-is-long-enough")


def test_audit_export_fails_closed_without_bucket_object_lock(monkeypatch):
    _settings(monkeypatch)
    client = FakeS3(lock={"ObjectLockConfiguration": {"ObjectLockEnabled": "Disabled"}})
    monkeypatch.setattr(audit_export, "_s3_client", lambda: client)

    with pytest.raises(audit_export.AuditExportUnavailable, match="Object Lock enabled"):
        audit_export.store_immutable_audit_export("tenant-a", _events())
    assert client.put_args is None


def test_audit_export_fails_closed_without_bucket_versioning(monkeypatch):
    _settings(monkeypatch)
    client = FakeS3(versioning={"Status": "Suspended"})
    monkeypatch.setattr(audit_export, "_s3_client", lambda: client)

    with pytest.raises(audit_export.AuditExportUnavailable, match="versioning must be enabled"):
        audit_export.store_immutable_audit_export("tenant-a", _events())
    assert client.put_args is None


def test_audit_export_rejects_empty_batch(monkeypatch):
    _settings(monkeypatch)
    with pytest.raises(audit_export.AuditExportUnavailable, match="at least one event"):
        audit_export.store_immutable_audit_export("tenant-a", [])


def test_audit_export_signature_detects_payload_tampering(monkeypatch):
    _settings(monkeypatch)
    payload = {"tenant_id": "tenant-a", "event_count": 1, "signing_key_id": "test-key-v1"}
    digest, signature = audit_export.sign_audit_payload(payload, settings.audit_export_signing_key)
    document = {
        **payload,
        "payload_sha256": digest,
        "signature_algorithm": "HMAC-SHA256",
        "signature": signature,
    }
    assert audit_export.verify_audit_export_signature(document, settings.audit_export_signing_key)
    document["event_count"] = 2
    assert not audit_export.verify_audit_export_signature(document, settings.audit_export_signing_key)


def test_audit_export_endpoint_uses_tenant_cursor_and_records_export(monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace

    import app.main as main
    from app.models import AuditEvent, Role

    event = SimpleNamespace(
        id=12,
        tenant_id="tenant-a",
        actor_user_id=7,
        entity_type="invoice",
        entity_id="99",
        action="created",
        details={"amount": "12.00"},
        created_at=datetime(2026, 11, 16, tzinfo=timezone.utc),
        previous_hash="0" * 64,
        event_hash="a" * 64,
    )
    captured = {"filters": [], "events": [], "committed": False, "stored": []}

    class Query:
        def filter(self, *conditions):
            captured["filters"].extend(str(condition) for condition in conditions)
            return self

        def order_by(self, *args):
            return self

        def limit(self, value):
            captured["limit"] = value
            return self

        def all(self):
            return [event]

    class DB:
        def query(self, model):
            assert model is AuditEvent
            return Query()

        def commit(self):
            captured["committed"] = True

    monkeypatch.setattr(main, "verify_tenant_chain", lambda db, tenant_id: {
        "valid": True, "verified_events": 12, "head_hash": "a" * 64,
    })

    def store(tenant_id, events):
        captured["stored"].append((tenant_id, events))
        return {
            "key": "tenants/tenant-a/audit-exports/12-12.json",
            "event_count": 1,
            "first_event_id": 12,
            "last_event_id": 12,
            "payload_sha256": "b" * 64,
            "version_id": "version-123",
            "object_lock_mode": "COMPLIANCE",
            "retain_until": "2033-11-16T00:00:00+00:00",
        }

    monkeypatch.setattr(main, "store_immutable_audit_export", store)
    monkeypatch.setattr(main, "record_event", lambda *args: captured["events"].append(args))
    actor = SimpleNamespace(id=7, tenant_id="tenant-a", role=Role.admin)

    result = main.export_audit_events(after_id=11, batch_size=25, db=DB(), user=actor)

    assert result["status"] == "exported"
    assert result["next_after_id"] == 12
    assert result["version_id"] == "version-123"
    assert captured["limit"] == 25
    assert any("audit_events.tenant_id" in item for item in captured["filters"])
    assert any("audit_events.id >" in item for item in captured["filters"])
    assert captured["stored"][0][0] == "tenant-a"
    assert captured["stored"][0][1][0]["id"] == 12
    assert captured["events"][0][4] == "immutable_export_created"
    assert captured["committed"] is True


def test_audit_export_endpoint_refuses_corrupt_chain_before_storage(monkeypatch):
    from fastapi import HTTPException
    from types import SimpleNamespace

    import app.main as main
    from app.models import Role

    captured = {"stored": False, "queried": False}

    class DB:
        def query(self, model):
            captured["queried"] = True
            raise AssertionError("No audit rows should be read after chain verification fails")

    monkeypatch.setattr(main, "verify_tenant_chain", lambda db, tenant_id: {
        "valid": False, "verified_events": 3, "failed_event_id": 4,
        "reason": "event_hash_mismatch",
    })
    monkeypatch.setattr(
        main, "store_immutable_audit_export",
        lambda *args: captured.update(stored=True),
    )
    actor = SimpleNamespace(id=7, tenant_id="tenant-a", role=Role.admin)

    with pytest.raises(HTTPException) as exc:
        main.export_audit_events(after_id=0, batch_size=100, db=DB(), user=actor)

    assert exc.value.status_code == 409
    assert captured == {"stored": False, "queried": False}


def test_audit_export_keyring_verifies_retired_key_after_rotation():
    old_key = "old-signing-key-material-with-32-bytes"
    new_key = "new-signing-key-material-with-32-bytes"
    payload = {"tenant_id": "tenant-a", "event_count": 1, "signing_key_id": "audit-key-2026-01"}
    digest, signature = audit_export.sign_audit_payload(payload, old_key)
    document = {**payload, "payload_sha256": digest, "signature_algorithm": "HMAC-SHA256", "signature": signature}
    keyring = {"audit-key-2026-01": old_key, "audit-key-2026-11": new_key}
    assert audit_export.verify_audit_export_signature_with_keyring(document, keyring)


def test_audit_export_keyring_rejects_unknown_or_tampered_key_id():
    key = "old-signing-key-material-with-32-bytes"
    payload = {"tenant_id": "tenant-a", "event_count": 1, "signing_key_id": "audit-key-2026-01"}
    digest, signature = audit_export.sign_audit_payload(payload, key)
    document = {**payload, "payload_sha256": digest, "signature_algorithm": "HMAC-SHA256", "signature": signature}
    assert not audit_export.verify_audit_export_signature_with_keyring(document, {"audit-key-2026-11": key})
    tampered = {**document, "signing_key_id": "audit-key-2026-11"}
    assert not audit_export.verify_audit_export_signature_with_keyring(tampered, {"audit-key-2026-01": key, "audit-key-2026-11": key})
    assert not audit_export.verify_audit_export_signature_with_keyring(document, {})
