"""Signed, immutable audit exports to an S3 Object Lock bucket."""
import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from app.audit import calculate_event_hash, event_payload

from app.config import settings
from app.services.storage import _s3_client


class AuditExportUnavailable(RuntimeError):
    pass


# Bound synchronous export memory use and S3 object size. Larger audit histories
# must be exported as multiple verified batches using the after_id cursor.
MAX_AUDIT_EXPORT_BYTES = 10 * 1024 * 1024


def canonical_json(value: dict) -> bytes:
    """Serialize only strict JSON values; reject NaN/Infinity and implicit coercion."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AuditExportUnavailable("Audit export contains values that are not strict JSON") from exc


def sign_audit_payload(payload: dict, signing_key: str) -> tuple[str, str]:
    if not isinstance(signing_key, str) or len(signing_key) < 32:
        raise AuditExportUnavailable("AUDIT_EXPORT_SIGNING_KEY must contain at least 32 characters")
    encoded = canonical_json(payload)
    digest = hashlib.sha256(encoded).hexdigest()
    signature = hmac.new(signing_key.encode("utf-8"), encoded, hashlib.sha256).hexdigest()
    return digest, signature


def verify_audit_export_signature(document: dict, signing_key: str) -> bool:
    if not isinstance(document, dict) or not isinstance(signing_key, str):
        return False
    signature = document.get("signature")
    digest = document.get("payload_sha256")
    payload = {key: value for key, value in document.items() if key not in ("signature", "payload_sha256", "signature_algorithm")}
    try:
        expected_digest, expected_signature = sign_audit_payload(payload, signing_key)
    except (AuditExportUnavailable, TypeError, ValueError, AttributeError):
        return False
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
    if not isinstance(document, dict) or not isinstance(keyring, dict):
        return False
    key_id = document.get("signing_key_id")
    if not isinstance(key_id, str) or not key_id:
        return False
    signing_key = keyring.get(key_id)
    if not isinstance(signing_key, str) or len(signing_key) < 32:
        return False
    try:
        return verify_audit_export_signature(document, signing_key)
    except (AuditExportUnavailable, TypeError, ValueError, AttributeError):
        return False

def _is_sha256_hex(value: object) -> bool:
    """Accept only canonical lowercase SHA-256 digests."""
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _is_bounded_text(value: object, maximum: int) -> bool:
    return isinstance(value, str) and 0 < len(value) <= maximum and value.strip() == value


def verify_audit_export_document(document: dict, keyring: dict[str, str]) -> bool:
    """Verify signature, strict export metadata, and every event hash-chain link."""
    if not verify_audit_export_signature_with_keyring(document, keyring):
        return False
    try:
        events = document.get("events")
        tenant_id = document.get("tenant_id")
        event_count = document.get("event_count")
        first_event_id = document.get("first_event_id")
        last_event_id = document.get("last_event_id")
        retention_days = document.get("retention_days")
        if not isinstance(events, list) or not events or not _is_bounded_text(tenant_id, 64):
            return False
        if document.get("format") != "ledgergate-audit-export-v1":
            return False
        if not isinstance(event_count, int) or isinstance(event_count, bool) or event_count != len(events):
            return False
        if not isinstance(first_event_id, int) or isinstance(first_event_id, bool) or first_event_id < 1:
            return False
        if not isinstance(last_event_id, int) or isinstance(last_event_id, bool) or last_event_id < first_event_id:
            return False
        if not isinstance(retention_days, int) or isinstance(retention_days, bool) or not 1 <= retention_days <= 3650:
            return False
        export_id = document.get("export_id")
        if not isinstance(export_id, str):
            return False
        try:
            # Export IDs are generated as UUIDs and are part of the immutable
            # object identity; accept only their canonical lowercase form.
            if str(uuid.UUID(export_id)) != export_id:
                return False
        except (ValueError, AttributeError):
            return False
        if not _is_bounded_text(document.get("signing_key_id"), 128):
            return False
        exported_at = document.get("exported_at")
        if not isinstance(exported_at, str):
            return False
        exported_timestamp = datetime.fromisoformat(exported_at.replace("Z", "+00:00"))
        if exported_timestamp.tzinfo is None or exported_timestamp.utcoffset() is None:
            return False

        expected_previous = events[0].get("previous_hash") if isinstance(events[0], dict) else None
        if not _is_sha256_hex(expected_previous):
            return False
        if document.get("first_previous_hash") != expected_previous or not _is_sha256_hex(document.get("first_previous_hash")):
            return False

        previous_id = None
        for event in events:
            if not isinstance(event, dict) or event.get("tenant_id") != tenant_id:
                return False
            actor_id = event.get("actor_user_id")
            if actor_id is not None and (
                not isinstance(actor_id, int) or isinstance(actor_id, bool) or actor_id < 1
            ):
                return False
            if not _is_bounded_text(event.get("entity_type"), 80):
                return False
            if not _is_bounded_text(event.get("entity_id"), 80):
                return False
            if not _is_bounded_text(event.get("action"), 100):
                return False
            if not isinstance(event.get("details"), dict):
                return False
            event_id = event.get("id")
            if not isinstance(event_id, int) or isinstance(event_id, bool) or event_id < 1:
                return False
            if previous_id is not None and event_id <= previous_id:
                return False
            previous_id = event_id
            if event.get("previous_hash") != expected_previous or not _is_sha256_hex(event.get("previous_hash")):
                return False

            created_at = event.get("created_at")
            if not isinstance(created_at, str):
                return False
            timestamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                return False
            payload = event_payload(
                tenant_id=event["tenant_id"],
                actor_user_id=event.get("actor_user_id"),
                entity_type=event.get("entity_type"),
                entity_id=event.get("entity_id"),
                action=event.get("action"),
                details=event.get("details"),
                created_at=timestamp,
            )
            calculated = calculate_event_hash(expected_previous, payload)
            stored_hash = event.get("event_hash")
            if not _is_sha256_hex(stored_hash) or not hmac.compare_digest(stored_hash, calculated):
                return False
            expected_previous = stored_hash

        return (
            first_event_id == events[0]["id"]
            and last_event_id == events[-1]["id"]
            and _is_sha256_hex(document.get("last_event_hash"))
            and hmac.compare_digest(document["last_event_hash"], expected_previous)
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return False

def store_immutable_audit_export(tenant_id: str, events: list[dict], *, retention_days: int | None = None) -> dict:
    bucket = settings.audit_worm_bucket.strip()
    signing_key = settings.audit_export_signing_key
    retention = settings.audit_worm_retention_days if retention_days is None else retention_days
    if not bucket:
        raise AuditExportUnavailable("AUDIT_WORM_BUCKET is not configured")
    if not isinstance(tenant_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tenant_id) is None:
        raise AuditExportUnavailable("Tenant ID must be a safe, bounded storage path segment")
    if not isinstance(settings.audit_export_signing_key_id, str) or not _is_bounded_text(settings.audit_export_signing_key_id, 128):
        raise AuditExportUnavailable("AUDIT_EXPORT_SIGNING_KEY_ID is invalid")
    if not isinstance(signing_key, str) or len(signing_key) < 32:
        raise AuditExportUnavailable("AUDIT_EXPORT_SIGNING_KEY must contain at least 32 characters")
    if not isinstance(retention, int) or isinstance(retention, bool) or not 1 <= retention <= 3650:
        raise AuditExportUnavailable("Audit export retention must be an integer between 1 and 3650 days")
    if not isinstance(events, list) or not events:
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
    # Every export attempt gets a fresh immutable object key. Reusing a
    # range-only key makes retries ambiguous when Object Lock/versioning is on
    # and the database commit after a successful S3 write later fails.
    export_id = str(uuid.uuid4())
    payload = {
        "format": "ledgergate-audit-export-v1",
        "export_id": export_id,
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
    # Validate the exact signed representation before making an irreversible
    # COMPLIANCE-mode write. This protects the storage boundary even when a
    # future caller bypasses the API's database-chain verification.
    if not verify_audit_export_document(
        document,
        {settings.audit_export_signing_key_id: signing_key},
    ):
        raise AuditExportUnavailable("Audit export events or metadata failed strict validation")
    body = canonical_json(document)
    if len(body) > MAX_AUDIT_EXPORT_BYTES:
        raise AuditExportUnavailable(
            f"Audit export exceeds the {MAX_AUDIT_EXPORT_BYTES}-byte object limit; export a smaller batch"
        )
    key = (
        f"tenants/{tenant_id}/audit-exports/"
        f"{events[0]['id']}-{events[-1]['id']}/{export_id}.json"
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
        "export_id": export_id,
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
