"""Private invoice storage with S3-compatible encryption and mandatory ClamAV scanning.

Local storage is intended for development only. Production config validation requires
S3-compatible storage and a configured ClamAV daemon.
"""
import os
import re
import socket
import struct
import uuid
from pathlib import Path
from urllib.parse import urlparse
import boto3
from botocore.config import Config
from app.config import settings

class MalwareDetected(ValueError):
    pass

class StorageUnavailable(RuntimeError):
    pass


MAX_CLAMAV_RESPONSE_BYTES = 4096
TENANT_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
INVOICE_OBJECT_PATTERN = re.compile(
    r"tenants/(?P<tenant>[A-Za-z0-9_-]{1,64})/invoices/(?P<name>[0-9a-f]{32})\.(?P<suffix>pdf|txt)\Z"
)


def _validate_tenant_id(tenant_id: str) -> None:
    if not isinstance(tenant_id, str) or TENANT_ID_PATTERN.fullmatch(tenant_id) is None:
        raise ValueError("Tenant ID must be a safe, bounded storage path segment")


def _parse_s3_invoice_location(tenant_id: str, location: str) -> tuple[str, str, str]:
    _validate_tenant_id(tenant_id)
    if not isinstance(location, str) or not location:
        raise FileNotFoundError
    try:
        parsed = urlparse(location)
    except ValueError as exc:
        raise FileNotFoundError from exc
    if (
        parsed.scheme != "s3"
        or parsed.netloc != settings.s3_bucket
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.params
        or "\\" in location
        or "%" in parsed.path
    ):
        raise FileNotFoundError
    key = parsed.path.lstrip("/")
    match = INVOICE_OBJECT_PATTERN.fullmatch(key)
    if match is None or match.group("tenant") != tenant_id:
        raise FileNotFoundError
    return parsed.netloc, key, f".{match.group('suffix')}"


def scan_with_clamav(content: bytes) -> None:
    if not settings.clamav_host:
        if settings.app_env.lower() == "production":
            raise StorageUnavailable("Malware scanning is mandatory in production")
        return
    try:
        with socket.create_connection((settings.clamav_host, settings.clamav_port), timeout=settings.clamav_timeout_seconds) as sock:
            sock.settimeout(settings.clamav_timeout_seconds)
            sock.sendall(b"zINSTREAM\0")
            for offset in range(0, len(content), 64 * 1024):
                chunk = content[offset:offset + 64 * 1024]
                sock.sendall(struct.pack("!I", len(chunk)) + chunk)
            sock.sendall(struct.pack("!I", 0))
            response = b""
            while not response.endswith(b"\0"):
                part = sock.recv(4096)
                if not part:
                    break
                response += part
                # The daemon should return one short status line. Bound the
                # response so a faulty or hostile scanner cannot grow memory
                # without limit while an upload request is waiting.
                if len(response) > MAX_CLAMAV_RESPONSE_BYTES:
                    raise StorageUnavailable("Malware scanner response exceeded the allowed size")
    except (OSError, TimeoutError) as exc:
        raise StorageUnavailable("Malware scanner is unavailable; upload rejected") from exc
    if not response.endswith(b"\0"):
        raise StorageUnavailable("Malware scanner returned an invalid response; upload rejected")
    result = response.decode("utf-8", errors="replace").strip("\0 ")
    if result.endswith(" FOUND"):
        raise MalwareDetected("Uploaded document was identified as malware")
    if not result.endswith(" OK"):
        raise StorageUnavailable("Malware scanner returned an invalid response; upload rejected")

def _s3_client():
    kwargs = {"region_name": settings.s3_region, "config": Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"})}
    if settings.s3_endpoint_url:
        kwargs["endpoint_url"] = settings.s3_endpoint_url
    if settings.s3_access_key_id:
        kwargs["aws_access_key_id"] = settings.s3_access_key_id
    if settings.s3_secret_access_key:
        kwargs["aws_secret_access_key"] = settings.s3_secret_access_key
    return boto3.client("s3", **kwargs)

def store_invoice(tenant_id: str, suffix: str, content: bytes, *, already_scanned: bool = False) -> str:
    _validate_tenant_id(tenant_id)
    if suffix not in {".pdf", ".txt"}:
        raise ValueError("Invoice storage accepts only PDF and TXT suffixes")
    if not isinstance(content, bytes):
        raise ValueError("Invoice content must be bytes")
    # Upload handlers scan before document extraction. Keep the default fail-closed
    # behavior for every other caller, but avoid sending the same bytes through
    # ClamAV twice on the normal upload path.
    if not already_scanned:
        scan_with_clamav(content)
    key = f"tenants/{tenant_id}/invoices/{uuid.uuid4().hex}{suffix}"
    if settings.storage_backend.lower() == "s3":
        if not settings.s3_bucket:
            raise StorageUnavailable("S3_BUCKET is not configured")
        try:
            _s3_client().put_object(
                Bucket=settings.s3_bucket, Key=key, Body=content,
                ServerSideEncryption="AES256",
                ContentType="application/pdf" if suffix == ".pdf" else "text/plain",
                Metadata={"tenant-id": tenant_id},
            )
        except Exception as exc:
            raise StorageUnavailable("Private object storage write failed") from exc
        return f"s3://{settings.s3_bucket}/{key}"
    tenant_dir = Path(settings.upload_dir) / tenant_id
    tenant_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        tenant_dir.chmod(0o700)
    except OSError:
        pass
    path = tenant_dir / Path(key).name
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stored_file:
        stored_file.write(content)
    return str(path)


def delete_invoice(tenant_id: str, location: str) -> None:
    """Remove an uncommitted invoice object after a failed database insert."""
    if location.startswith("s3://"):
        bucket, key, _suffix = _parse_s3_invoice_location(tenant_id, location)
        try:
            _s3_client().delete_object(Bucket=bucket, Key=key)
        except Exception as exc:
            raise StorageUnavailable("Private object storage cleanup failed") from exc
        return
    tenant_dir = (Path(settings.upload_dir) / tenant_id).resolve()
    file_path = Path(location).resolve()
    if file_path.parent != tenant_dir:
        raise FileNotFoundError
    file_path.unlink(missing_ok=True)

def read_invoice(tenant_id: str, location: str) -> tuple[bytes, str]:
    if location.startswith("s3://"):
        bucket, key, suffix = _parse_s3_invoice_location(tenant_id, location)
        body = None
        try:
            response = _s3_client().get_object(Bucket=bucket, Key=key)
            body = response["Body"]
            # Bound memory use even if an object was written outside the upload
            # endpoint or its metadata does not match the configured upload cap.
            max_bytes = settings.max_upload_mb * 1024 * 1024
            content = body.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise StorageUnavailable("Stored invoice exceeds the configured download size limit")
            return content, suffix
        except StorageUnavailable:
            raise
        except Exception as exc:
            raise StorageUnavailable("Private object storage read failed") from exc
        finally:
            if body is not None:
                body.close()
    tenant_dir = (Path(settings.upload_dir) / tenant_id).resolve()
    file_path = Path(location).resolve()
    if file_path.parent != tenant_dir or not file_path.is_file():
        raise FileNotFoundError
    return file_path.read_bytes(), file_path.suffix.lower()
