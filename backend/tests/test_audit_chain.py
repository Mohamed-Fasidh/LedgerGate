import pytest
from app.audit import GENESIS_HASH, calculate_event_hash, event_payload
from datetime import datetime, timezone

def test_audit_event_hash_is_deterministic_and_chained():
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    payload = event_payload(
        tenant_id="tenant-a", actor_user_id=7, entity_type="invoice",
        entity_id=42, action="approved", details={"amount": 1200},
        created_at=created,
    )
    first = calculate_event_hash(GENESIS_HASH, payload)
    assert first == calculate_event_hash(GENESIS_HASH, payload)
    assert first != calculate_event_hash("f" * 64, payload)
    changed = dict(payload, action="rejected")
    assert first != calculate_event_hash(GENESIS_HASH, changed)


def test_audit_chain_reports_verified_prefix_before_corruption():
    from types import SimpleNamespace
    from app.audit import verify_tenant_chain

    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    previous = GENESIS_HASH
    for index in range(2):
        payload = event_payload(
            tenant_id="tenant-a", actor_user_id=7, entity_type="invoice",
            entity_id=index + 1, action="created", details={"index": index},
            created_at=created,
        )
        digest = calculate_event_hash(previous, payload)
        rows.append(SimpleNamespace(
            tenant_id="tenant-a", actor_user_id=7, entity_type="invoice",
            entity_id=str(index + 1), action="created", details={"index": index},
            created_at=created, previous_hash=previous, event_hash=digest, id=index + 1,
        ))
        previous = digest

    corrupted = SimpleNamespace(**{**rows[1].__dict__, "id": 3, "previous_hash": "f" * 64})
    rows.append(corrupted)

    class Query:
        def filter(self, *args, **kwargs):
            return self
        def order_by(self, *args, **kwargs):
            return self
        def limit(self, *args, **kwargs):
            return self
        def all(self):
            return rows

    class DB:
        def query(self, *args, **kwargs):
            return Query()

    result = verify_tenant_chain(DB(), "tenant-a")
    assert result["valid"] is False
    assert result["verified_events"] == 2
    assert result["failed_event_id"] == 3
    assert result["reason"] == "previous_hash_mismatch"


def test_valid_audit_chain_reports_exact_verified_head_id():
    from types import SimpleNamespace
    from app.audit import verify_tenant_chain

    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    payload = event_payload(
        tenant_id="tenant-a", actor_user_id=7, entity_type="invoice",
        entity_id=1, action="created", details={"id": 1}, created_at=created,
    )
    digest = calculate_event_hash(GENESIS_HASH, payload)
    row = SimpleNamespace(
        id=42, tenant_id="tenant-a", actor_user_id=7, entity_type="invoice",
        entity_id="1", action="created", details={"id": 1}, created_at=created,
        previous_hash=GENESIS_HASH, event_hash=digest,
    )

    class Query:
        def filter(self, *args, **kwargs):
            return self
        def order_by(self, *args, **kwargs):
            return self
        def all(self):
            return [row]

    class DB:
        def query(self, *args, **kwargs):
            return Query()

    result = verify_tenant_chain(DB(), "tenant-a")
    assert result["valid"] is True
    assert result["last_event_id"] == 42


def test_empty_audit_chain_reports_no_verified_head():
    from app.audit import verify_tenant_chain

    class Query:
        def filter(self, *args, **kwargs):
            return self
        def order_by(self, *args, **kwargs):
            return self
        def all(self):
            return []

    class DB:
        def query(self, *args, **kwargs):
            return Query()

    result = verify_tenant_chain(DB(), "tenant-a")
    assert result["valid"] is True
    assert result["last_event_id"] is None


def test_tenant_audit_chain_lock_is_transaction_scoped_for_postgres():
    from types import SimpleNamespace
    from app.audit import lock_tenant_audit_chain

    class DB:
        bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def __init__(self):
            self.calls = []

        def execute(self, statement, params):
            self.calls.append((str(statement), params))

    db = DB()
    lock_tenant_audit_chain(db, "tenant-a")

    assert len(db.calls) == 1
    assert "pg_advisory_xact_lock" in db.calls[0][0]
    assert db.calls[0][1] == {"tenant_id": "tenant-a"}


def test_tenant_audit_chain_lock_is_skipped_for_non_postgres():
    from types import SimpleNamespace
    from app.audit import lock_tenant_audit_chain

    class DB:
        bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

        def execute(self, *args, **kwargs):
            raise AssertionError("PostgreSQL advisory lock must not run on SQLite")

    lock_tenant_audit_chain(DB(), "tenant-a")


def test_audit_chain_verification_reads_bounded_pages():
    from types import SimpleNamespace
    from app.audit import verify_tenant_chain
    from app.models import AuditEvent

    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    previous = GENESIS_HASH
    for index in range(5):
        payload = event_payload(
            tenant_id="tenant-a", actor_user_id=7, entity_type="invoice",
            entity_id=index + 1, action="created", details={"index": index},
            created_at=created,
        )
        digest = calculate_event_hash(previous, payload)
        rows.append(SimpleNamespace(
            id=(index + 1) * 3, tenant_id="tenant-a", actor_user_id=7,
            entity_type="invoice", entity_id=str(index + 1), action="created",
            details={"index": index}, created_at=created,
            previous_hash=previous, event_hash=digest,
        ))
        previous = digest

    class Query:
        def __init__(self):
            self.after_id = None
            self.page_size = None
        def filter(self, expression):
            if getattr(expression.left, "key", None) == "id":
                self.after_id = expression.right.value
            return self
        def order_by(self, *args, **kwargs):
            return self
        def limit(self, value):
            self.page_size = value
            return self
        def all(self):
            selected = [row for row in rows if self.after_id is None or row.id > self.after_id]
            return selected[:self.page_size]

    class DB:
        def __init__(self):
            self.queries = []
        def query(self, model):
            query = Query()
            self.queries.append(query)
            return query

    db = DB()
    result = verify_tenant_chain(db, "tenant-a", batch_size=2)
    assert result["valid"] is True
    assert result["verified_events"] == 5
    assert result["last_event_id"] == 15
    assert len(db.queries) == 3
    assert all(query.page_size == 2 for query in db.queries)


def test_audit_chain_verification_rejects_invalid_page_size():
    from app.audit import verify_tenant_chain

    with pytest.raises(ValueError):
        verify_tenant_chain(object(), "tenant-a", batch_size=0)
