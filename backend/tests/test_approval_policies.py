import pytest
from fastapi import HTTPException
from app.main import _approval_ranges_overlap, _validate_approval_steps

def test_approval_steps_accept_sequential_roles():
    assert _validate_approval_steps([["reviewer"], ["admin"]]) == [["reviewer"], ["admin"]]
    assert _validate_approval_steps([["reviewer", "admin"]]) == [["reviewer", "admin"]]

@pytest.mark.parametrize("steps", [
    [],
    [[]],
    [["analyst"]],
    [["reviewer", "reviewer"]],
    [["reviewer", "admin", "reviewer"]],
    [["reviewer"]] * 6,
])
def test_approval_steps_reject_invalid_role_sequences(steps):
    with pytest.raises(HTTPException) as exc:
        _validate_approval_steps(steps)
    assert exc.value.status_code == 400

def test_approval_policy_amount_ranges_are_half_open():
    assert _approval_ranges_overlap(0, 100, 99, 200)
    assert not _approval_ranges_overlap(0, 100, 100, 200)
    assert _approval_ranges_overlap(100, None, 150, 200)
    assert not _approval_ranges_overlap(0, 100, 100, None)

def test_approval_policy_deactivation_is_tenant_scoped_locked_and_audited(monkeypatch):
    from types import SimpleNamespace
    import app.main as main
    from app.models import ApprovalPolicy, Role

    policy = SimpleNamespace(
        id=17, tenant_id="tenant-a", name="Legacy approvals", is_active=True,
    )
    captured = {"conditions": None, "locked": False, "events": [], "committed": False}

    class Query:
        def filter(self, *conditions):
            captured["conditions"] = conditions
            return self
        def with_for_update(self):
            captured["locked"] = True
            return self
        def first(self):
            return policy

    class DB:
        def query(self, model):
            assert model is ApprovalPolicy
            return Query()
        def commit(self):
            captured["committed"] = True

    monkeypatch.setattr(main, "record_event", lambda *args: captured["events"].append(args))
    actor = SimpleNamespace(id=7, tenant_id="tenant-a", role=Role.admin)

    result = main.deactivate_approval_policy(
        policy_id=17, idempotency_key=None, db=DB(), user=actor,
    )

    assert result == {"id": 17, "is_active": False}
    assert policy.is_active is False
    assert captured["locked"] is True
    assert captured["committed"] is True
    assert captured["events"][0][4] == "deactivated"
    rendered = [str(condition) for condition in captured["conditions"]]
    assert any("approval_policies.tenant_id" in item for item in rendered)
    assert any("approval_policies.id" in item for item in rendered)


def test_approval_policy_deactivation_is_idempotent_for_inactive_policy(monkeypatch):
    from types import SimpleNamespace
    import app.main as main
    from app.models import ApprovalPolicy, Role

    policy = SimpleNamespace(
        id=18, tenant_id="tenant-a", name="Already inactive", is_active=False,
    )
    captured = {"events": []}

    class Query:
        def filter(self, *conditions): return self
        def with_for_update(self): return self
        def first(self): return policy

    class DB:
        def query(self, model):
            assert model is ApprovalPolicy
            return Query()

    monkeypatch.setattr(main, "record_event", lambda *args: captured["events"].append(args))
    actor = SimpleNamespace(id=7, tenant_id="tenant-a", role=Role.admin)

    result = main.deactivate_approval_policy(
        policy_id=18, idempotency_key=None, db=DB(), user=actor,
    )

    assert result == {"id": 18, "is_active": False}
    assert captured["events"] == []


def test_invoice_reconciliation_is_limited_to_admin_and_analyst_roles():
    from types import SimpleNamespace
    from app.main import app
    from app.models import Role

    route = next(
        route for route in app.routes
        if getattr(route, "path", None) == "/api/v1/invoices/{invoice_id}/reconcile"
    )
    user_dependency = next(
        dependency for dependency in route.dependant.dependencies
        if dependency.name == "user"
    )
    authorize = user_dependency.call

    for role in (Role.admin, Role.analyst):
        assert authorize(user=SimpleNamespace(role=role)) is not None

    with pytest.raises(HTTPException) as exc:
        authorize(user=SimpleNamespace(role=Role.reviewer))
    assert exc.value.status_code == 403


def test_approval_policy_mutation_lock_is_tenant_scoped_on_postgres():
    from types import SimpleNamespace
    import app.main as main

    captured = {}
    class DB:
        bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
        def execute(self, statement, params):
            captured["sql"] = str(statement)
            captured["params"] = params

    main._lock_approval_policy_ranges(DB(), "tenant-secure")
    assert "pg_advisory_xact_lock" in captured["sql"]
    assert "approval_policy_ranges" in captured["sql"]
    assert captured["params"] == {"tenant_id": "tenant-secure"}


def test_approval_policy_mutation_lock_is_skipped_for_non_postgres():
    from types import SimpleNamespace
    import app.main as main

    class DB:
        bind = SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
        def execute(self, *args, **kwargs):
            pytest.fail("PostgreSQL advisory locks must not run on SQLite")

    main._lock_approval_policy_ranges(DB(), "tenant-secure")


@pytest.mark.parametrize(
    ("max_amount", "expected_filter_count"),
    [(None, 4), ("500.00", 5)],
)
def test_overlap_lookup_is_tenant_scoped_and_uses_half_open_bounds(max_amount, expected_filter_count):
    from decimal import Decimal
    from app.models import ApprovalPolicy
    import app.main as main

    captured = {"conditions": []}
    class Query:
        def filter(self, *conditions):
            captured["conditions"].extend(conditions)
            return self
        def first(self):
            return None

    class DB:
        def query(self, model):
            assert model is ApprovalPolicy
            return Query()

    result = main._find_overlapping_approval_policy(
        DB(), "tenant-a", "INR", Decimal("100.00"), Decimal(max_amount) if max_amount is not None else None,
    )
    assert result is None
    assert len(captured["conditions"]) == expected_filter_count
    rendered = [str(condition) for condition in captured["conditions"]]
    assert any("approval_policies.tenant_id" in item for item in rendered)
    assert any("approval_policies.currency" in item for item in rendered)
    assert any("approval_policies.is_active" in item for item in rendered)
    assert any("approval_policies.max_amount" in item for item in rendered)
    if max_amount is not None:
        assert any("approval_policies.min_amount" in item for item in rendered)


def test_approval_policy_create_rechecks_idempotency_after_tenant_lock(monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace
    import app.main as main

    replay = {"id": 31, "name": "Existing policy", "is_active": True}
    calls = []
    def lookup(db, tenant_id, key, operation, fingerprint):
        calls.append(("lookup", len(calls)))
        return None if len(calls) == 1 else replay
    def lock(db, tenant_id):
        calls.append(("lock", tenant_id))

    monkeypatch.setattr(main, "_get_idempotent_response", lookup)
    monkeypatch.setattr(main, "_lock_approval_policy_ranges", lock)

    payload = SimpleNamespace(
        model_dump=lambda: {"name": "Existing policy", "currency": "INR", "min_amount": "0", "max_amount": None,
                            "approval_steps": [["reviewer"]]},
        name="Existing policy",
        currency="INR",
        min_amount=Decimal("0.00"),
        max_amount=None,
        approval_steps=[["reviewer"]],
    )
    result = main.create_approval_policy(
        payload=payload,
        idempotency_key="policy-create-0001",
        db=object(),
        user=SimpleNamespace(id=7, tenant_id="tenant-a"),
    )

    assert result == replay
    assert [item[0] for item in calls] == ["lookup", "lock", "lookup"]


@pytest.mark.parametrize(
    ("status", "allowed", "expected_status"),
    [
        ("extraction_review", {"extraction_review", "received"}, None),
        ("received", {"extraction_review", "received"}, None),
        ("approved", {"extraction_review", "received"}, 409),
        ("rejected", {"extraction_review", "received"}, 409),
        ("review_required", {"received"}, 409),
        ("processing", {"received"}, 409),
    ],
)
def test_invoice_state_guard_rejects_invalid_transitions(status, allowed, expected_status):
    from types import SimpleNamespace
    from app.main import _require_invoice_status
    from app.models import InvoiceStatus

    invoice = SimpleNamespace(status=InvoiceStatus(status))
    allowed_statuses = {InvoiceStatus(value) for value in allowed}
    if expected_status is None:
        assert _require_invoice_status(invoice, allowed_statuses, "invalid transition") is None
    else:
        with pytest.raises(HTTPException) as exc:
            _require_invoice_status(invoice, allowed_statuses, "invalid transition")
        assert exc.value.status_code == expected_status


def test_extraction_confirmation_and_reconciliation_enforce_workflow_state_guard():
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "app" / "main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    guarded_routes = {
        "confirm_extraction": False,
        "reconcile": False,
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in guarded_routes:
            guarded_routes[node.name] = any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "_require_invoice_status"
                for call in ast.walk(node)
            )
    assert guarded_routes == {"confirm_extraction": True, "reconcile": True}


def test_approval_policy_currency_is_normalized_and_validated():
    from pydantic import ValidationError
    from app.schemas import ApprovalPolicyIn

    payload = ApprovalPolicyIn(name="USD approvals", currency=" usd ", approval_steps=[["reviewer"]])
    assert payload.currency == "USD"
    with pytest.raises(ValidationError):
        ApprovalPolicyIn(name="Bad currency", currency="US1", approval_steps=[["reviewer"]])


@pytest.mark.parametrize("invoice_currency", ["USD", "EUR", "INR"])
def test_invoice_approval_plan_filters_policy_by_invoice_currency(invoice_currency):
    from decimal import Decimal
    from types import SimpleNamespace
    import app.main as main
    from app.models import ApprovalPolicy, Role

    policy = SimpleNamespace(
        id=91,
        currency=invoice_currency,
        approval_steps=[["reviewer"], ["admin"]],
        min_amount=Decimal("0.00"),
        is_active=True,
    )
    captured = {"conditions": []}

    class Query:
        def filter(self, *conditions):
            captured["conditions"].extend(conditions)
            return self
        def order_by(self, *ordering):
            return self
        def first(self):
            return policy

    class DB:
        def query(self, model):
            assert model is ApprovalPolicy
            return Query()

    invoice = SimpleNamespace(
        approval_steps_snapshot=None,
        amount=Decimal("250.00"),
        currency=invoice_currency,
        approval_policy_id=None,
        approval_stage=0,
    )
    actor = SimpleNamespace(tenant_id="tenant-a", role=Role.reviewer)

    result = main._resolve_invoice_approval_plan(DB(), invoice, actor)

    assert result == policy.approval_steps
    assert invoice.approval_policy_id == policy.id
    assert invoice.approval_steps_snapshot == policy.approval_steps
    assert invoice.approval_stage == 1
    rendered = [str(condition) for condition in captured["conditions"]]
    assert any(f"approval_policies.currency = '{invoice_currency}'" in item for item in rendered)
