"""Encrypted database-backed outbox with bounded retries and crash recovery."""
import json
import logging
import time
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from sqlalchemy import and_, or_

from app.config import settings
from app.database import SessionLocal
from app.models import OutboundEmail
from app.services.email import send_email_verification_email, send_password_reset_email

logger = logging.getLogger(__name__)
LEASE_SECONDS = 300
MAX_ATTEMPTS = 12
ALLOWED_TYPES = {"email_verification", "password_reset"}


def _now():
    return datetime.now(timezone.utc)


def _cipher():
    key = settings.email_outbox_encryption_key
    if not key and settings.app_env.lower() != "production":
        # Local development fallback only. Production startup rejects a missing dedicated key.
        import base64
        import hashlib
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.jwt_secret_key.encode()).digest()).decode()
    if not key:
        raise RuntimeError("Email outbox encryption is not configured")
    return Fernet(key.encode("ascii"))


def _decrypt_payload(encrypted_payload: str) -> dict:
    """Decrypt with the active key or explicitly configured previous rotation keys."""
    keys = [settings.email_outbox_encryption_key]
    keys.extend(key.strip() for key in settings.email_outbox_decryption_keys.split(",") if key.strip())
    if not keys[0] and settings.app_env.lower() != "production":
        try:
            return json.loads(_cipher().decrypt(encrypted_payload.encode("ascii")))
        except Exception:
            pass
    last_error = None
    for key in keys:
        if not key:
            continue
        try:
            return json.loads(Fernet(key.encode("ascii")).decrypt(encrypted_payload.encode("ascii")))
        except Exception as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise RuntimeError("Email outbox decryption keys are not configured")


def enqueue_email(db, recipient: str, message_type: str, url: str) -> OutboundEmail:
    """Add the encrypted message to the caller's transaction; caller commits atomically."""
    if message_type not in ALLOWED_TYPES:
        raise ValueError("Unsupported account email type")
    payload = _cipher().encrypt(json.dumps({"url": url}, separators=(",", ":")).encode()).decode("ascii")
    row = OutboundEmail(
        recipient=recipient,
        message_type=message_type,
        encrypted_payload=payload,
        status="pending",
        attempts=0,
        max_attempts=MAX_ATTEMPTS,
        next_attempt_at=_now(),
    )
    db.add(row)
    return row


def _send(row):
    payload = _decrypt_payload(row.encrypted_payload)
    sender = {
        "email_verification": send_email_verification_email,
        "password_reset": send_password_reset_email,
    }[row.message_type]
    sender(row.recipient, payload["url"])


def process_one(session_factory=SessionLocal, sender=_send, now=None) -> bool:
    """Claim and process one message. Uses SKIP LOCKED for multi-worker safety."""
    now = now or _now()
    stale_before = now - timedelta(seconds=LEASE_SECONDS)
    with session_factory() as db:
        row = db.query(OutboundEmail).filter(
            or_(
                and_(OutboundEmail.status == "pending", OutboundEmail.next_attempt_at <= now),
                and_(OutboundEmail.status == "sending", OutboundEmail.locked_at <= stale_before),
            )
        ).order_by(OutboundEmail.next_attempt_at, OutboundEmail.id).with_for_update(skip_locked=True).first()
        if row is None:
            db.rollback()
            return False
        row.status = "sending"
        row.attempts += 1
        row.locked_at = now
        row.last_error = None
        row_id = row.id
        db.commit()

    try:
        with session_factory() as db:
            row = db.get(OutboundEmail, row_id)
            if row is None or row.status != "sending":
                return True
            # Copy only what is needed outside the database session.
            message = OutboundEmail(
                recipient=row.recipient,
                message_type=row.message_type,
                encrypted_payload=row.encrypted_payload,
            )
            attempt = row.attempts
            max_attempts = row.max_attempts
        sender(message)
    except Exception as exc:
        with session_factory() as db:
            row = db.query(OutboundEmail).filter(OutboundEmail.id == row_id).with_for_update().first()
            if row is not None and row.status == "sending":
                row.last_error = type(exc).__name__[:120]
                row.locked_at = None
                if row.attempts >= row.max_attempts:
                    row.status = "dead"
                else:
                    delay = min(3600, 30 * (2 ** max(0, row.attempts - 1)))
                    row.status = "pending"
                    row.next_attempt_at = now + timedelta(seconds=delay)
                db.commit()
        logger.warning("Account email delivery failed; message_id=%s error_type=%s", row_id, type(exc).__name__)
        return True

    with session_factory() as db:
        row = db.query(OutboundEmail).filter(OutboundEmail.id == row_id).with_for_update().first()
        if row is not None and row.status == "sending":
            row.status = "sent"
            row.sent_at = _now()
            row.locked_at = None
            row.last_error = None
            db.commit()
    return True


def run_forever(poll_seconds: float = 2.0):
    logger.info("Account email outbox worker started")
    while True:
        try:
            processed = process_one()
            if not processed:
                time.sleep(poll_seconds)
        except KeyboardInterrupt:
            logger.info("Account email outbox worker stopping")
            return
        except Exception:
            logger.exception("Account email outbox worker iteration failed")
            time.sleep(poll_seconds)
