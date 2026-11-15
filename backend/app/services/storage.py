"""Private document storage backends.

Local storage is intended for development only. Production deployments must use
a private S3-compatible bucket and short-lived presigned downloads.
"""
from pathlib import Path
import re
import uuid

import boto3
from botocore.config import Config as BotoConfig

from app.config import settings


_TENANT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_DOCUMENT_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}/[0-9a-f]{32}\.(?:pdf|txt)$")


def _tenant_prefix(tenant_id: str) -> str:
    if not isinstance(tenant_id, str) or not _TENANT_ID.fullmatch(tenant_id):
        raise ValueError("Invalid tenant identifier")
    return tenant_id


def _validate_document_key(key: str, tenant_id: str) -> str:
    """Accept only canonical generated keys owned by the requested tenant."""
    prefix = _tenant_prefix(tenant_id) + "/"
    if not isinstance(key, str) or not _DOCUMENT_KEY.fullmatch(key) or not key.startswith(prefix):
        raise FileNotFoundError("Document not found")
    return key


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url or None,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id or None,
        aws_secret_access_key=settings.s3_secret_access_key or None,
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path" if settings.s3_force_path_style else "virtual"}),
    )


def save_document(content: bytes, tenant_id: str, suffix: str) -> str:
    """Persist bytes and return an opaque tenant-scoped object key."""
    prefix = _tenant_prefix(tenant_id)
    if suffix not in {".pdf", ".txt"}:
        raise ValueError("Unsupported document suffix")
    key = f"{prefix}/{uuid.uuid4().hex}{suffix}"
    if settings.storage_backend == "s3":
        if not settings.s3_bucket:
            raise RuntimeError("S3_BUCKET is required for S3 storage")
        _s3_client().put_object(
            Bucket=settings.s3_bucket,
            Key=key,
            Body=content,
            ContentType="application/pdf" if suffix == ".pdf" else "text/plain; charset=utf-8",
            ServerSideEncryption="AES256",
        )
        return key

    root = Path(settings.upload_dir).resolve()
    tenant_dir = root / prefix
    # Never follow a pre-existing tenant-directory symlink, even if it points
    # somewhere else inside the upload root.
    if tenant_dir.is_symlink():
        raise ValueError("Invalid storage directory")
    tenant_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if tenant_dir.is_symlink():
        raise ValueError("Invalid storage directory")
    destination = root / key
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Generated storage key already exists")
    try:
        destination.parent.chmod(0o700)
    except OSError:
        pass
    fd = __import__("os").open(
        destination, __import__("os").O_WRONLY | __import__("os").O_CREAT |
        __import__("os").O_EXCL | getattr(__import__("os"), "O_BINARY", 0), 0o600
    )
    with __import__("os").fdopen(fd, "wb") as stored:
        stored.write(content)
    return key


def local_document_path(key: str, tenant_id: str) -> Path:
    prefix = _tenant_prefix(tenant_id) + "/"
    if not key.startswith(prefix):
        raise FileNotFoundError("Document not found")
    root = Path(settings.upload_dir).resolve()
    candidate = root / key
    tenant_dir = root / tenant_id
    # Reject symlinks at the tenant root and object itself, then verify the
    # fully resolved path remains inside the configured storage root.
    if tenant_dir.is_symlink() or candidate.is_symlink():
        raise FileNotFoundError("Document not found")
    try:
        path = candidate.resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        raise FileNotFoundError("Document not found") from None
    if root not in path.parents or not path.is_file():
        raise FileNotFoundError("Document not found")
    return path

def create_download_url(key: str, tenant_id: str) -> str:
    """Create a short-lived URL only after the caller tenant-checks the invoice."""
    prefix = _tenant_prefix(tenant_id) + "/"
    if not key.startswith(prefix):
        raise FileNotFoundError("Document not found")
    if settings.storage_backend != "s3" or not settings.s3_bucket:
        raise RuntimeError("Presigned URLs are available only with S3 storage")
    return _s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=settings.s3_presigned_url_expiry_seconds,
    )


def delete_document(key: str, tenant_id: str) -> None:
    """Delete a tenant-owned document when its database transaction fails."""
    prefix = _tenant_prefix(tenant_id) + "/"
    if not key.startswith(prefix):
        raise FileNotFoundError("Document not found")
    if settings.storage_backend == "s3":
        if not settings.s3_bucket:
            raise RuntimeError("S3_BUCKET is required for S3 storage")
        _s3_client().delete_object(Bucket=settings.s3_bucket, Key=key)
        return
    path = local_document_path(key, tenant_id)
    path.unlink(missing_ok=True)


def list_document_keys() -> set[str]:
    """Return only LedgerGate-generated document keys from the configured store."""
    if settings.storage_backend == "s3":
        if not settings.s3_bucket:
            raise RuntimeError("S3_BUCKET is required for S3 storage")
        keys = set()
        for page in _s3_client().get_paginator("list_objects_v2").paginate(Bucket=settings.s3_bucket):
            for item in page.get("Contents", []):
                key = item.get("Key", "")
                if _DOCUMENT_KEY.fullmatch(key):
                    keys.add(key)
        return keys

    root = Path(settings.upload_dir).resolve()
    if not root.exists():
        return set()
    keys = set()
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            key = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if _DOCUMENT_KEY.fullmatch(key):
            keys.add(key)
    return keys
