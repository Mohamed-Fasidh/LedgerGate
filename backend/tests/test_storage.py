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



class FakeClamAVSocket:
    def __init__(self, response):
        self.response = response
        self.sent = bytearray()
        self.timeout = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendall(self, data):
        self.sent.extend(data)

    def recv(self, size):
        chunk, self.response = self.response[:size], self.response[size:]
        return chunk


def test_clamav_scanner_accepts_bounded_ok_response(monkeypatch):
    fake = FakeClamAVSocket(b"stream: OK\0")
    monkeypatch.setattr(storage.settings, "clamav_host", "clamav.test")
    monkeypatch.setattr(storage.settings, "clamav_port", 3310)
    monkeypatch.setattr(storage, "socket", type("SocketModule", (), {"create_connection": lambda *_args, **_kwargs: fake})())

    storage.scan_with_clamav(b"invoice bytes")
    assert fake.sent.startswith(b"zINSTREAM\0")
    assert fake.timeout == storage.settings.clamav_timeout_seconds


def test_clamav_scanner_rejects_oversized_response(monkeypatch):
    fake = FakeClamAVSocket(b"A" * (storage.MAX_CLAMAV_RESPONSE_BYTES + 1))
    monkeypatch.setattr(storage.settings, "clamav_host", "clamav.test")
    monkeypatch.setattr(storage.settings, "clamav_port", 3310)
    monkeypatch.setattr(storage, "socket", type("SocketModule", (), {"create_connection": lambda *_args, **_kwargs: fake})())

    with pytest.raises(storage.StorageUnavailable, match="response exceeded"):
        storage.scan_with_clamav(b"invoice bytes")


def test_clamav_scanner_rejects_unterminated_response(monkeypatch):
    fake = FakeClamAVSocket(b"stream: OK")
    monkeypatch.setattr(storage.settings, "clamav_host", "clamav.test")
    monkeypatch.setattr(storage.settings, "clamav_port", 3310)
    monkeypatch.setattr(storage, "socket", type("SocketModule", (), {"create_connection": lambda *_args, **_kwargs: fake})())

    with pytest.raises(storage.StorageUnavailable, match="invalid response"):
        storage.scan_with_clamav(b"invoice bytes")
