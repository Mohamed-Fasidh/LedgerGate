import os
from pathlib import Path

import pytest

from app.services import storage


def test_local_storage_round_trip_and_restrictive_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "upload_dir", str(tmp_path))

    key = storage.save_document(b"invoice text", "tenant-a", ".txt")

    assert key.startswith("tenant-a/")
    assert storage.local_document_path(key, "tenant-a").read_bytes() == b"invoice text"
    if os.name != "nt":
        assert (tmp_path / "tenant-a").stat().st_mode & 0o777 == 0o700
        assert storage.local_document_path(key, "tenant-a").stat().st_mode & 0o777 == 0o600


def test_local_storage_rejects_cross_tenant_download(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.settings, "upload_dir", str(tmp_path))
    key = storage.save_document(b"private", "tenant-a", ".txt")

    with pytest.raises(FileNotFoundError):
        storage.local_document_path(key, "tenant-b")


@pytest.mark.parametrize("tenant_id", ["../outside", "tenant/a", "", "x" * 65])
def test_storage_rejects_invalid_tenant_identifiers(tenant_id):
    with pytest.raises(ValueError, match="tenant"):
        storage.save_document(b"content", tenant_id, ".txt")


def test_storage_rejects_unsupported_suffix():
    with pytest.raises(ValueError, match="suffix"):
        storage.save_document(b"content", "tenant-a", ".exe")


def test_local_storage_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.settings, "upload_dir", str(tmp_path))

    with pytest.raises(FileNotFoundError):
        storage.local_document_path("tenant-a/../../outside.txt", "tenant-a")


def test_s3_upload_uses_private_server_side_encryption(monkeypatch):
    calls = {}
    class FakeS3:
        def put_object(self, **kwargs):
            calls.update(kwargs)

    monkeypatch.setattr(storage.settings, "storage_backend", "s3")
    monkeypatch.setattr(storage.settings, "s3_bucket", "ledgergate-private")
    monkeypatch.setattr(storage, "_s3_client", lambda: FakeS3())

    key = storage.save_document(b"%PDF-private", "tenant-a", ".pdf")

    assert key.startswith("tenant-a/")
    assert calls["Bucket"] == "ledgergate-private"
    assert calls["Key"] == key
    assert calls["ServerSideEncryption"] == "AES256"
    assert calls["ContentType"] == "application/pdf"


def test_s3_presigned_download_is_tenant_scoped_and_short_lived(monkeypatch):
    calls = {}
    class FakeS3:
        def generate_presigned_url(self, operation, *, Params, ExpiresIn):
            calls.update(operation=operation, params=Params, expires=ExpiresIn)
            return "https://private.example/signed"

    monkeypatch.setattr(storage.settings, "storage_backend", "s3")
    monkeypatch.setattr(storage.settings, "s3_bucket", "ledgergate-private")
    monkeypatch.setattr(storage.settings, "s3_presigned_url_expiry_seconds", 180)
    monkeypatch.setattr(storage, "_s3_client", lambda: FakeS3())

    url = storage.create_download_url("tenant-a/document.pdf", "tenant-a")

    assert url == "https://private.example/signed"
    assert calls == {
        "operation": "get_object",
        "params": {"Bucket": "ledgergate-private", "Key": "tenant-a/document.pdf"},
        "expires": 180,
    }
    with pytest.raises(FileNotFoundError):
        storage.create_download_url("tenant-a/document.pdf", "tenant-b")


def test_s3_storage_requires_bucket(monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_backend", "s3")
    monkeypatch.setattr(storage.settings, "s3_bucket", "")

    with pytest.raises(RuntimeError, match="S3_BUCKET"):
        storage.save_document(b"content", "tenant-a", ".txt")


def test_delete_document_removes_local_tenant_owned_file(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "upload_dir", str(tmp_path))
    key = storage.save_document(b"temporary invoice", "tenant-a", ".txt")

    storage.delete_document(key, "tenant-a")

    assert not (tmp_path / key).exists()
    with pytest.raises(FileNotFoundError):
        storage.local_document_path(key, "tenant-a")


def test_delete_document_rejects_cross_tenant_key():
    with pytest.raises(FileNotFoundError):
        storage.delete_document("tenant-a/document.txt", "tenant-b")


def test_delete_document_removes_s3_object(monkeypatch):
    calls = {}
    class FakeS3:
        def delete_object(self, **kwargs):
            calls.update(kwargs)

    monkeypatch.setattr(storage.settings, "storage_backend", "s3")
    monkeypatch.setattr(storage.settings, "s3_bucket", "ledgergate-private")
    monkeypatch.setattr(storage, "_s3_client", lambda: FakeS3())

    storage.delete_document("tenant-a/document.txt", "tenant-a")

    assert calls == {"Bucket": "ledgergate-private", "Key": "tenant-a/document.txt"}


def test_list_document_keys_ignores_non_generated_and_symlinked_local_files(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "upload_dir", str(tmp_path))
    generated = storage.save_document(b"invoice", "tenant-a", ".txt")
    (tmp_path / "tenant-a" / "notes.txt").write_text("not an application object")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside")
    (tmp_path / "tenant-a" / ("f" * 32 + ".pdf")).symlink_to(outside)

    assert storage.list_document_keys() == {generated}


def test_list_document_keys_lists_only_generated_s3_keys(monkeypatch):
    class FakePaginator:
        def paginate(self, **kwargs):
            assert kwargs == {"Bucket": "ledgergate-private"}
            return [{"Contents": [
                {"Key": "tenant-a/" + "a" * 32 + ".pdf"},
                {"Key": "tenant-a/not-ledgergate.txt"},
            ]}]

    class FakeS3:
        def get_paginator(self, name):
            assert name == "list_objects_v2"
            return FakePaginator()

    monkeypatch.setattr(storage.settings, "storage_backend", "s3")
    monkeypatch.setattr(storage.settings, "s3_bucket", "ledgergate-private")
    monkeypatch.setattr(storage, "_s3_client", lambda: FakeS3())

    assert storage.list_document_keys() == {"tenant-a/" + "a" * 32 + ".pdf"}



def test_local_storage_rejects_tenant_directory_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "upload_dir", str(tmp_path))
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "document.txt").write_text("outside")
    (tmp_path / "tenant-a").symlink_to(outside, target_is_directory=True)

    with pytest.raises(FileNotFoundError):
        storage.local_document_path("tenant-a/document.txt", "tenant-a")


def test_local_storage_rejects_object_symlink(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "upload_dir", str(tmp_path))
    tenant_dir = tmp_path / "tenant-a"
    tenant_dir.mkdir()
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("outside")
    (tenant_dir / "document.txt").symlink_to(outside)

    with pytest.raises(FileNotFoundError):
        storage.local_document_path("tenant-a/document.txt", "tenant-a")





def test_failed_document_cleanup_is_logged_without_sensitive_details(monkeypatch, caplog):
    import logging
    from app import main

    def fail_cleanup(key, tenant_id):
        raise RuntimeError("sensitive-object-key-and-bucket")

    monkeypatch.setattr(main, "delete_document", fail_cleanup)
    with caplog.at_level(logging.ERROR, logger=main.__name__):
        result = main._cleanup_uploaded_document("tenant-secret/object-secret.pdf", "tenant-secret")

    assert result is False
    assert "Invoice document cleanup failed" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "tenant-secret" not in caplog.text
    assert "object-secret" not in caplog.text
    assert "sensitive-object-key-and-bucket" not in caplog.text


def test_successful_document_cleanup_does_not_log(monkeypatch, caplog):
    import logging
    from app import main

    calls = []
    monkeypatch.setattr(main, "delete_document", lambda key, tenant_id: calls.append((key, tenant_id)))
    with caplog.at_level(logging.ERROR, logger=main.__name__):
        result = main._cleanup_uploaded_document("tenant-a/object.pdf", "tenant-a")

    assert result is True
    assert calls == [("tenant-a/object.pdf", "tenant-a")]
    assert caplog.text == ""


@pytest.mark.skipif(
    __import__("os").environ.get("LEDGERGATE_S3_INTEGRATION") != "1",
    reason="Set LEDGERGATE_S3_INTEGRATION=1 to run against a real private S3 bucket",
)
def test_real_s3_upload_download_and_delete_round_trip():
    import uuid
    from urllib.parse import parse_qs, urlparse
    from botocore.exceptions import ClientError

    if storage.settings.storage_backend != "s3" or not storage.settings.s3_bucket:
        pytest.skip("Configure STORAGE_BACKEND=s3 and S3_BUCKET for integration testing")

    tenant_id = f"ci-{uuid.uuid4().hex[:16]}"
    content = b"LedgerGate private S3 integration test"
    key = storage.save_document(content, tenant_id, ".txt")
    client = storage._s3_client()
    try:
        response = client.get_object(Bucket=storage.settings.s3_bucket, Key=key)
        assert response["Body"].read() == content
        assert response.get("ServerSideEncryption") == "AES256"
        url = storage.create_download_url(key, tenant_id)
        parsed = urlparse(url)
        assert parsed.scheme == ("http" if storage.settings.s3_endpoint_url and parsed.scheme == "http" else "https")
        query = parse_qs(parsed.query)
        assert "X-Amz-Signature" in query
        assert "X-Amz-Credential" in query
    finally:
        storage.delete_document(key, tenant_id)
    with pytest.raises(ClientError) as missing:
        client.head_object(Bucket=storage.settings.s3_bucket, Key=key)
    assert missing.value.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}


@pytest.mark.parametrize(
    "key",
    [
        "tenant-a/../outside.txt",
        "tenant-a/not-a-generated-name.txt",
        "tenant-a/" + "g" * 32 + ".pdf",
        "tenant-a/" + "a" * 32 + ".exe",
        "tenant-a//" + "a" * 32 + ".pdf",
    ],
)
def test_storage_rejects_noncanonical_document_keys(key, monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_backend", "s3")
    monkeypatch.setattr(storage.settings, "s3_bucket", "ledgergate-private")

    with pytest.raises(FileNotFoundError):
        storage.create_download_url(key, "tenant-a")
    with pytest.raises(FileNotFoundError):
        storage.delete_document(key, "tenant-a")
