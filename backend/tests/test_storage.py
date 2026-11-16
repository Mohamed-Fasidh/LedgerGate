import pytest
from app.services import storage

def test_local_storage_round_trip_is_tenant_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(storage.settings, "clamav_host", "")
    monkeypatch.setattr(storage.settings, "app_env", "test")

    location = storage.store_invoice("tenant-a", ".txt", b"invoice total 100")
    body, suffix = storage.read_invoice("tenant-a", location)

    assert body == b"invoice total 100"
    assert suffix == ".txt"
    with pytest.raises(FileNotFoundError):
        storage.read_invoice("tenant-b", location)

def test_upload_fails_closed_when_scanner_detects_malware(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(storage, "scan_with_clamav", lambda _content: (_ for _ in ()).throw(storage.MalwareDetected("Eicar test signature")))

    with pytest.raises(storage.MalwareDetected):
        storage.store_invoice("tenant-a", ".txt", b"malware test")
    assert not list(tmp_path.rglob("*"))


def test_local_storage_delete_is_tenant_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(storage.settings, "clamav_host", "")
    monkeypatch.setattr(storage.settings, "app_env", "test")

    location = storage.store_invoice("tenant-a", ".txt", b"temporary invoice")
    storage.delete_invoice("tenant-a", location)
    assert not __import__("pathlib").Path(location).exists()

    another = storage.store_invoice("tenant-a", ".txt", b"keep this")
    with pytest.raises(FileNotFoundError):
        storage.delete_invoice("tenant-b", another)
    assert __import__("pathlib").Path(another).exists()
