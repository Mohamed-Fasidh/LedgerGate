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
    monkeypatch.setattr(settings, "audit_worm_retention_days", 365)


def test_audit_export_is_signed_and_written_in_compliance_mode(monkeypatch):
    _settings(monkeypatch)
    client = FakeS3()
    monkeypatch.setattr(audit_export, "_s3_client", lambda: client)

    result = audit_export.store_immutable_audit_export("tenant-a", _events())
    args = client.put_args
    document = json.loads(args["Body"])

    assert result["object_lock_mode"] == "COMPLIANCE"
    assert args["ObjectLockMode"] == "COMPLIANCE"
    assert args["ServerSideEncryption"] == "AES256"
    assert args["Bucket"] == "ledgergate-audit-worm"
    assert args["ObjectLockRetainUntilDate"] > datetime.now(timezone.utc)
    assert document["event_count"] == 1
    assert document["signing_key_id"] == settings.audit_export_signing_key_id
    assert audit_export.verify_audit_export_signature(document, settings.audit_export_signing_key)
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
