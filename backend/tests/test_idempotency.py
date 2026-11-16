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


def test_idempotency_response_is_normalized_for_json_storage():
    from datetime import date
    from app.main import _store_idempotency_response

    class DB:
        def __init__(self):
            self.added = []
        def add(self, value):
            self.added.append(value)

    db = DB()
    _store_idempotency_response(
        db, "tenant-a", "request-0002", "invoice.confirm_extraction",
        "c" * 64, {"invoice_date": date(2026, 11, 16)},
    )
    assert db.added[0].response_json == {"invoice_date": "2026-11-16"}


def test_expired_idempotency_cleanup_is_tenant_scoped_and_bounded():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from app.models import IdempotencyRecord
    from app.main import _delete_expired_idempotency_records

    expired = [SimpleNamespace(id=1), SimpleNamespace(id=2)]
    captured = {}

    class Query:
        def filter(self, *conditions):
            captured["conditions"] = conditions
            return self
        def order_by(self, *columns):
            captured["order_by"] = columns
            return self
        def limit(self, amount):
            captured["limit"] = amount
            return self
        def all(self):
            return expired

    class DB:
        def query(self, model):
            assert model is IdempotencyRecord
            return Query()
        def delete(self, record):
            captured.setdefault("deleted", []).append(record)

    db = DB()
    cutoff = datetime(2026, 8, 18, tzinfo=timezone.utc)
    deleted = _delete_expired_idempotency_records(db, "tenant-a", cutoff, batch_size=25)

    assert deleted == 2
    assert captured["limit"] == 25
    assert captured["deleted"] == expired
    rendered = [str(condition) for condition in captured["conditions"]]
    assert any("idempotency_records.tenant_id" in item for item in rendered)
    assert any("idempotency_records.created_at" in item for item in rendered)


def test_expired_idempotency_cleanup_handles_empty_batch():
    from datetime import datetime, timezone
    from app.main import _delete_expired_idempotency_records

    class Query:
        def filter(self, *conditions): return self
        def order_by(self, *columns): return self
        def limit(self, amount): return self
        def all(self): return []

    class DB:
        def query(self, model): return Query()
        def delete(self, record): raise AssertionError("No records should be deleted")

    assert _delete_expired_idempotency_records(
        DB(), "tenant-empty", datetime.now(timezone.utc), batch_size=10
    ) == 0



def test_invoice_key_is_reserved_against_other_write_operations():
    from types import SimpleNamespace
    from app.main import _get_idempotent_response

    record = SimpleNamespace(
        operation="invoice.create",
        request_hash="d" * 64,
        response_json={"id": 17},
    )

    class Query:
        def filter(self, *args, **kwargs): return self
        def first(self): return record

    class DB:
        def query(self, *args, **kwargs): return Query()

    with pytest.raises(HTTPException) as exc:
        _get_idempotent_response(
            DB(), "tenant-a", "shared-key-0001", "supplier.create", "d" * 64
        )
    assert exc.value.status_code == 409


def test_invoice_idempotency_response_is_stored_in_shared_namespace():
    from app.main import _store_idempotency_response

    class DB:
        def __init__(self): self.added = []
        def add(self, value): self.added.append(value)

    db = DB()
    response = {"id": 17, "invoice_number": "INV-17"}
    _store_idempotency_response(
        db, "tenant-a", "invoice-key-0001", "invoice.create",
        "e" * 64, response,
    )
    assert len(db.added) == 1
    assert db.added[0].tenant_id == "tenant-a"
    assert db.added[0].key == "invoice-key-0001"
    assert db.added[0].operation == "invoice.create"
    assert db.added[0].response_json == response


def test_tenant_user_fingerprint_hashes_password_without_exposing_it():
    from app.main import _tenant_user_request_hash

    password = "very-secret-password-123"
    first = _tenant_user_request_hash("ADMIN@example.com", password, "analyst")
    assert first == _tenant_user_request_hash("admin@example.com", password, "analyst")
    assert first != _tenant_user_request_hash("admin@example.com", "different-password-123", "analyst")
    assert password not in first
    assert len(first) == 64
