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
