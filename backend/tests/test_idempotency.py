import pytest
from fastapi import HTTPException
from app.main import _request_fingerprint, _validate_idempotency_key

def test_request_fingerprint_is_stable_and_payload_sensitive():
    payload = {"filename": "invoice.pdf", "content_sha256": "abc123"}
    first = _request_fingerprint("upload", payload)
    assert first == _request_fingerprint("upload", {"content_sha256": "abc123", "filename": "invoice.pdf"})
    assert first != _request_fingerprint("upload", {"filename": "invoice.pdf", "content_sha256": "different"})
    assert first != _request_fingerprint("create", payload)

@pytest.mark.parametrize("key", ["short", "contains spaces", "bad\nkey", "é" * 8, "x" * 129])
def test_invalid_idempotency_keys_are_rejected(key):
    with pytest.raises(HTTPException) as exc:
        _validate_idempotency_key(key)
    assert exc.value.status_code == 400

def test_valid_idempotency_key_is_accepted():
    assert _validate_idempotency_key("upload-key-0001") is None
    assert _validate_idempotency_key(None) is None


def test_idempotency_replays_only_matching_operation_and_fingerprint():
    from types import SimpleNamespace
    from app.main import _get_idempotent_response

    response = {"id": 42, "name": "Acme"}
    record = SimpleNamespace(
        operation="supplier.create",
        request_hash="a" * 64,
        response_json=response,
    )

    class Query:
        def filter(self, *args, **kwargs):
            return self
        def first(self):
            return record

    class DB:
        def query(self, *args, **kwargs):
            return Query()

    assert _get_idempotent_response(DB(), "tenant-a", "request-0001", "supplier.create", "a" * 64) == response
    with pytest.raises(HTTPException) as exc:
        _get_idempotent_response(DB(), "tenant-a", "request-0001", "purchase_order.create", "a" * 64)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        _get_idempotent_response(DB(), "tenant-a", "request-0001", "supplier.create", "b" * 64)
    assert exc.value.status_code == 409
