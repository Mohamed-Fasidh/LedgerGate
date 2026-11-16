import pytest
from fastapi import HTTPException
from types import SimpleNamespace


class BoundedListQuery:
    def __init__(self, captured):
        self.captured = captured

    def filter(self, *conditions):
        self.captured["conditions"] = conditions
        return self

    def order_by(self, *columns):
        self.captured["ordered"] = True
        return self

    def offset(self, value):
        self.captured["offset"] = value
        return self

    def limit(self, value):
        self.captured["limit"] = value
        return self

    def all(self):
        self.captured["all_called"] = True
        return []


class BoundedListDB:
    def __init__(self, captured):
        self.captured = captured

    def query(self, model):
        self.captured["model"] = model
        return BoundedListQuery(self.captured)


@pytest.mark.parametrize(
    ("endpoint", "model_name", "kwargs"),
    [
        ("list_suppliers", "Supplier", {}),
        ("list_pos", "PurchaseOrder", {}),
        ("list_invoices", "Invoice", {"status": None}),
        ("list_approval_policies", "ApprovalPolicy", {}),
        ("audit_events", "AuditEvent", {}),
    ],
)
def test_tenant_collection_endpoints_apply_bounded_pagination(endpoint, model_name, kwargs):
    import app.main as main
    from app import models

    captured = {}
    actor = SimpleNamespace(id=7, tenant_id="tenant-a")
    handler = getattr(main, endpoint)

    result = handler(
        **kwargs,
        limit=25,
        offset=50,
        db=BoundedListDB(captured),
        user=actor,
    )

    assert result == []
    assert captured["model"] is getattr(models, model_name)
    assert captured["ordered"] is True
    assert captured["offset"] == 50
    assert captured["limit"] == 25
    assert captured["all_called"] is True
    rendered = [str(condition) for condition in captured["conditions"]]
    assert any(f"{getattr(models, model_name).__tablename__}.tenant_id" in item for item in rendered)


@pytest.mark.parametrize(
    ("endpoint", "kwargs"),
    [
        ("list_suppliers", {}),
        ("list_pos", {}),
        ("list_invoices", {"status": None}),
        ("list_approval_policies", {}),
        ("audit_events", {}),
    ],
)
@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (201, 0), (100, -1)])
def test_tenant_collection_endpoints_reject_invalid_pagination(endpoint, kwargs, limit, offset):
    import app.main as main

    class NoQueryDB:
        def query(self, model):
            pytest.fail("database must not be queried for invalid pagination")

    with pytest.raises(HTTPException) as error:
        getattr(main, endpoint)(
            **kwargs,
            limit=limit,
            offset=offset,
            db=NoQueryDB(),
            user=SimpleNamespace(id=7, tenant_id="tenant-a"),
        )
    assert error.value.status_code == 400
