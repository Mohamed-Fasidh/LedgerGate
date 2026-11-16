"""Signed, immutable audit exports to an S3 Object Lock bucket."""
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.services.storage import _s3_client


class AuditExportUnavailable(RuntimeError):
    pass


def canonical_json(value: dict) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")


def sign_audit_payload(payload: dict, signing_key: str) -> tuple[str, str]:
    if len(signing_key) < 32:
        raise AuditExportUnavailable("AUDIT_EXPORT_SIGNING_KEY must contain at least 32 characters")
    encoded = canonical_json(payload)
    digest = hashlib.sha256(encoded).hexdigest()
    signature = hmac.new(signing_key.encode("utf-8"), encoded, hashlib.sha256).hexdigest()
    return digest, signature


def verify_audit_export_signature(document: dict, signing_key: str) -> bool:
    signature = document.get("signature")
    digest = document.get("payload_sha256")
    payload = {key: value for key, value in document.items() if key not in ("signature", "payload_sha256", "signature_algorithm")}
    expected_digest, expected_signature = sign_audit_payload(payload, signing_key)
    return (
        document.get("signature_algorithm") == "HMAC-SHA256"
        and isinstance(signature, str)
        and isinstance(digest, str)
        and hmac.compare_digest(digest, expected_digest)
        and hmac.compare_digest(signature, expected_signature)
    )


def verify_audit_export_signature_with_keyring(document: dict, keyring: dict[str, str]) -> bool:
    """Verify an export with a key selected by its immutable signing key ID.

    Keep retired verification keys available for at least the retention period
    of exports signed with them. The keyring is supplied by the caller so key
    custody and secret-manager integration remain outside this module.
    """
    key_id = document.get("signing_key_id")
    if not isinstance(key_id, str) or not key_id or not isinstance(keyring, dict):
        return False
    signing_key = keyring.get(key_id)
    if not isinstance(signing_key, str) or len(signing_key) < 32:
        return False
    try:
        return verify_audit_export_signature(document, signing_key)
    except (AuditExportUnavailable, TypeError, ValueError):
        return False

def store_immutable_audit_export(tenant_id: str, events: list[dict], *, retention_days: int | None = None) -> dict:
    bucket = settings.audit_worm_bucket.strip()
    signing_key = settings.audit_export_signing_key
    retention = settings.audit_worm_retention_days if retention_days is None else retention_days
    if not bucket:
        raise AuditExportUnavailable("AUDIT_WORM_BUCKET is not configured")
    if not settings.audit_export_signing_key_id.strip():
        raise AuditExportUnavailable("AUDIT_EXPORT_SIGNING_KEY_ID is not configured")
    if len(signing_key) < 32:
        raise AuditExportUnavailable("AUDIT_EXPORT_SIGNING_KEY must contain at least 32 characters")
    if retention < 1 or retention > 3650:
        raise AuditExportUnavailable("Audit export retention must be between 1 and 3650 days")
    if not events:
        raise AuditExportUnavailable("An audit export must contain at least one event")

    client = _s3_client()
    try:
        versioning = client.get_bucket_versioning(Bucket=bucket)
        if versioning.get("Status") != "Enabled":
            raise AuditExportUnavailable("Audit WORM bucket versioning must be enabled")
        lock_response = client.get_object_lock_configuration(Bucket=bucket)
        lock_config = lock_response.get("ObjectLockConfiguration") or {}
        if lock_config.get("ObjectLockEnabled") != "Enabled":
            raise AuditExportUnavailable("Audit WORM bucket must have S3 Object Lock enabled")
    except AuditExportUnavailable:
        raise
    except Exception as exc:
        raise AuditExportUnavailable("Could not verify audit WORM bucket configuration") from exc

    now = datetime.now(timezone.utc)
    retain_until = now + timedelta(days=retention)
    payload = {
        "format": "ledgergate-audit-export-v1",
        "tenant_id": tenant_id,
        "exported_at": now.isoformat(),
        "retention_days": retention,
        "signing_key_id": settings.audit_export_signing_key_id,
        "first_event_id": events[0]["id"],
        "last_event_id": events[-1]["id"],
        "event_count": len(events),
        "first_previous_hash": events[0]["previous_hash"],
        "last_event_hash": events[-1]["event_hash"],
        "events": events,
    }
    digest, signature = sign_audit_payload(payload, signing_key)
    document = {
        **payload,
        "payload_sha256": digest,
        "signature_algorithm": "HMAC-SHA256",
        "signature": signature,
    }
    body = canonical_json(document)
    key = (
        f"tenants/{tenant_id}/audit-exports/"
        f"{events[0]['id']}-{events[-1]['id']}.json"
    )
    try:
        put_response = client.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
            ServerSideEncryption="AES256",
            ObjectLockMode="COMPLIANCE",
            ObjectLockRetainUntilDate=retain_until,
            Metadata={
                "tenant-id": tenant_id,
                "payload-sha256": digest,
                "signature-algorithm": "HMAC-SHA256",
                "signing-key-id": settings.audit_export_signing_key_id,
            },
        )
    except Exception as exc:
        raise AuditExportUnavailable("Immutable audit export write failed") from exc

    return {
        "bucket": bucket,
        "key": key,
        "version_id": put_response.get("VersionId"),
        "object_uri": f"s3://{bucket}/{key}",
        "payload_sha256": digest,
        "signature": signature,
        "signature_algorithm": "HMAC-SHA256",
        "signing_key_id": settings.audit_export_signing_key_id,
        "object_lock_mode": "COMPLIANCE",
        "retain_until": retain_until.isoformat(),
        "event_count": len(events),
        "first_event_id": events[0]["id"],
        "last_event_id": events[-1]["id"],
    }
