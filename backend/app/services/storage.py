"""Private invoice storage with S3-compatible encryption and mandatory ClamAV scanning.

Local storage is intended for development only. Production config validation requires
S3-compatible storage and a configured ClamAV daemon.
"""
import os
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

def scan_with_clamav(content: bytes) -> None:
    if not settings.clamav_host:
        if settings.app_env.lower() == "production":
            raise StorageUnavailable("Malware scanning is mandatory in production")
        return
    try:
        with socket.create_connection((settings.clamav_host, settings.clamav_port), timeout=settings.clamav_timeout_seconds) as sock:
            sock.settimeout(settings.clamav_timeout_seconds)
            sock.sendall(b"zINSTREAM\\0")
            for offset in range(0, len(content), 64 * 1024):
                chunk = content[offset:offset + 64 * 1024]
                sock.sendall(struct.pack("!I", len(chunk)) + chunk)
            sock.sendall(struct.pack("!I", 0))
            response = b""
            while not response.endswith(b"\\0"):
                part = sock.recv(4096)
                if not part:
                    break
                response += part
    except (OSError, TimeoutError) as exc:
        raise StorageUnavailable("Malware scanner is unavailable; upload rejected") from exc
    result = response.decode("utf-8", errors="replace").strip("\\0 ")
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

def store_invoice(tenant_id: str, suffix: str, content: bytes) -> str:
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

def read_invoice(tenant_id: str, location: str) -> tuple[bytes, str]:
    if location.startswith("s3://"):
        parsed = urlparse(location)
        if parsed.netloc != settings.s3_bucket or not parsed.path.lstrip("/").startswith(f"tenants/{tenant_id}/invoices/"):
            raise FileNotFoundError
        try:
            response = _s3_client().get_object(Bucket=parsed.netloc, Key=parsed.path.lstrip("/"))
            return response["Body"].read(), Path(parsed.path).suffix.lower()
        except Exception as exc:
            raise StorageUnavailable("Private object storage read failed") from exc
    tenant_dir = (Path(settings.upload_dir) / tenant_id).resolve()
    file_path = Path(location).resolve()
    if file_path.parent != tenant_dir or not file_path.is_file():
        raise FileNotFoundError
    return file_path.read_bytes(), file_path.suffix.lower()
