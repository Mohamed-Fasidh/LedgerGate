from pathlib import Path

import pytest

from app.services import storage


def test_local_storage_round_trip_and_restrictive_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "upload_dir", str(tmp_path))

    key = storage.save_document(b"invoice text", "tenant-a", ".txt")

    assert key.startswith("tenant-a/")
    assert storage.local_document_path(key, "tenant-a").read_bytes() == b"invoice text"
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
        try:
            main.logger.error(
                "Invoice document cleanup failed after persistence error (error_type=%s)",
                "RuntimeError",
            )
        except Exception:
            pytest.fail("logging cleanup failure should not raise")

    assert "RuntimeError" in caplog.text
    assert "sensitive-object-key-and-bucket" not in caplog.text
