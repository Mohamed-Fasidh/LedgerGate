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



def test_s3_invoice_read_is_bounded_and_closes_response_body(monkeypatch):
    import io
    monkeypatch.setattr(storage.settings, "storage_backend", "s3")
    monkeypatch.setattr(storage.settings, "s3_bucket", "private-invoices")
    monkeypatch.setattr(storage.settings, "max_upload_mb", 1)
    body = io.BytesIO(b"x" * (1024 * 1024 + 10))
    class FakeS3:
        def get_object(self, **kwargs):
            assert kwargs == {
                "Bucket": "private-invoices",
                "Key": "tenants/tenant-a/invoices/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf",
            }
            return {"Body": body}
    monkeypatch.setattr(storage, "_s3_client", lambda: FakeS3())

    with pytest.raises(storage.StorageUnavailable, match="size limit"):
        storage.read_invoice(
            "tenant-a",
            "s3://private-invoices/tenants/tenant-a/invoices/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf",
        )
    assert body.closed


def test_s3_invoice_read_accepts_bounded_content(monkeypatch):
    import io
    monkeypatch.setattr(storage.settings, "storage_backend", "s3")
    monkeypatch.setattr(storage.settings, "s3_bucket", "private-invoices")
    monkeypatch.setattr(storage.settings, "max_upload_mb", 1)
    body = io.BytesIO(b"%PDF-small")
    class FakeS3:
        def get_object(self, **kwargs):
            return {"Body": body}
    monkeypatch.setattr(storage, "_s3_client", lambda: FakeS3())

    content, suffix = storage.read_invoice(
        "tenant-a",
        "s3://private-invoices/tenants/tenant-a/invoices/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.pdf",
    )
    assert content == b"%PDF-small"
    assert suffix == ".pdf"
    assert body.closed


def test_storage_can_reuse_the_upload_handler_scan_result(tmp_path, monkeypatch):
    monkeypatch.setattr(storage.settings, "storage_backend", "local")
    monkeypatch.setattr(storage.settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(
        storage,
        "scan_with_clamav",
        lambda _content: (_ for _ in ()).throw(AssertionError("content was scanned twice")),
    )

    location = storage.store_invoice(
        "tenant-a",
        ".txt",
        b"already scanned invoice",
        already_scanned=True,
    )

    body, suffix = storage.read_invoice("tenant-a", location)
    assert body == b"already scanned invoice"
    assert suffix == ".txt"


@pytest.mark.parametrize(
    "tenant_id,suffix",
    [
        ("../tenant-b", ".pdf"),
        ("tenant/a", ".pdf"),
        ("tenant-a", ".pdf/../../other"),
        ("tenant-a", ".exe"),
    ],
)
def test_invoice_storage_rejects_unsafe_tenant_ids_and_suffixes_before_scanning(
    tenant_id, suffix, monkeypatch
):
    monkeypatch.setattr(
        storage,
        "scan_with_clamav",
        lambda _content: (_ for _ in ()).throw(AssertionError("invalid input reached scanner")),
    )
    with pytest.raises(ValueError):
        storage.store_invoice(tenant_id, suffix, b"invoice")


@pytest.mark.parametrize(
    "location",
    [
        "s3://private-invoices/tenants/tenant-a/invoices/not-a-uuid.pdf",
        "s3://private-invoices/tenants/tenant-b/invoices/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf",
        "s3://private-invoices/tenants/tenant-a/invoices/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf?versionId=latest",
        "s3://private-invoices/tenants/tenant-a/invoices/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf?",
        "s3://private-invoices/tenants/tenant-a/invoices/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf#fragment",
        "s3://private-invoices/tenants/tenant-a/invoices/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf#",
        "s3://private-invoices/tenants/tenant-a/invoices/%2e%2e/other.pdf",
        "s3://private-invoices/tenants/tenant-a/invoices/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.exe",
    ],
)
def test_s3_invoice_read_rejects_noncanonical_or_cross_tenant_locations(
    location, monkeypatch
):
    monkeypatch.setattr(storage.settings, "storage_backend", "s3")
    monkeypatch.setattr(storage.settings, "s3_bucket", "private-invoices")

    def unexpected_client():
        raise AssertionError("invalid object location reached S3")

    monkeypatch.setattr(storage, "_s3_client", unexpected_client)
    with pytest.raises(FileNotFoundError):
        storage.read_invoice("tenant-a", location)


def test_s3_invoice_delete_rejects_noncanonical_location_before_s3_call(monkeypatch):
    monkeypatch.setattr(storage.settings, "s3_bucket", "private-invoices")
    monkeypatch.setattr(
        storage,
        "_s3_client",
        lambda: (_ for _ in ()).throw(AssertionError("invalid object location reached S3")),
    )

    with pytest.raises(FileNotFoundError):
        storage.delete_invoice(
            "tenant-a",
            "s3://private-invoices/tenants/tenant-a/invoices/../../other.pdf",
        )


def test_s3_invoice_delete_accepts_only_canonical_tenant_object(monkeypatch):
    monkeypatch.setattr(storage.settings, "s3_bucket", "private-invoices")

    class FakeS3:
        def delete_object(self, **kwargs):
            assert kwargs == {
                "Bucket": "private-invoices",
                "Key": "tenants/tenant-a/invoices/cccccccccccccccccccccccccccccccc.txt",
            }

    monkeypatch.setattr(storage, "_s3_client", lambda: FakeS3())
    storage.delete_invoice(
        "tenant-a",
        "s3://private-invoices/tenants/tenant-a/invoices/cccccccccccccccccccccccccccccccc.txt",
    )
