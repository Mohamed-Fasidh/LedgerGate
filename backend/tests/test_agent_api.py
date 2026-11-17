import pytest

from app.main import _build_agent_orchestrator, run_agent_task
from app.schemas import AgentTaskIn


class FakeInferenceClient:
    pass


def test_agent_task_schema_trims_input_and_rejects_blank():
    assert AgentTaskIn(task="  Review invoice 42  ").task == "Review invoice 42"
    with pytest.raises(ValueError):
        AgentTaskIn(task="   ")
    with pytest.raises(ValueError):
        AgentTaskIn(task="     ")
    with pytest.raises(ValueError):
        AgentTaskIn(task="four")
    with pytest.raises(ValueError):
        AgentTaskIn(task="x" * 4001)


def test_agent_task_schema_rejects_client_supplied_tenant_scope():
    with pytest.raises(ValueError):
        AgentTaskIn(task="Review invoice 42", tenant_id="attacker-tenant")


def test_api_orchestrator_binds_planner_and_executor_to_authenticated_tenant(monkeypatch):
    monkeypatch.setattr(
        "app.main._get_agent_inference_client", lambda: FakeInferenceClient()
    )
    db = object()
    orchestrator = _build_agent_orchestrator(db, "tenant-from-auth-token")

    assert orchestrator.executor.db is db
    assert orchestrator.executor.tenant_id == "tenant-from-auth-token"
    assert orchestrator.planner.tool_executor is orchestrator.executor
    assert orchestrator.max_steps == 5
    assert orchestrator.timeout_seconds == 30.0


def test_agent_endpoint_uses_authenticated_tenant_and_returns_json_safe_result(monkeypatch):
    from types import SimpleNamespace

    captured = {}
    result = {
        "status": "completed",
        "outputs": [{"step": 1, "tool": "policy_search", "output": []}],
    }

    class FakeOrchestrator:
        def run(self, task):
            captured["task"] = task
            return result

    def build(_db, tenant_id):
        captured["tenant_id"] = tenant_id
        return FakeOrchestrator()

    monkeypatch.setattr("app.main._build_agent_orchestrator", build)
    response = run_agent_task(
        AgentTaskIn(task="Review invoice 42"),
        db=object(),
        user=SimpleNamespace(tenant_id="tenant-from-auth-token"),
    )

    assert captured == {
        "tenant_id": "tenant-from-auth-token",
        "task": "Review invoice 42",
    }
    assert response == result


def test_agent_endpoint_maps_planning_failure_to_service_unavailable(monkeypatch):
    from types import SimpleNamespace
    from fastapi import HTTPException

    class FakeOrchestrator:
        def run(self, _task):
            return SimpleNamespace(error_code="PLAN_FAILED")

    monkeypatch.setattr(
        "app.main._build_agent_orchestrator",
        lambda _db, _tenant_id: FakeOrchestrator(),
    )
    with pytest.raises(HTTPException) as exc:
        run_agent_task(
            AgentTaskIn(task="Review invoice 42"),
            db=object(),
            user=SimpleNamespace(tenant_id="tenant-a"),
        )

    assert exc.value.status_code == 503
    assert "provider" not in exc.value.detail.lower()


def test_agent_endpoint_requires_authentication_and_uses_only_read_only_tools():
    import ast
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1]
    main_source = (backend / "app" / "main.py").read_text(encoding="utf-8")
    service_source = (backend / "app" / "agent" / "service.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(main_source)
    route = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "run_agent_task"
    )
    decorators = [d for d in route.decorator_list if isinstance(d, ast.Call)]
    assert any(
        isinstance(d.func, ast.Attribute)
        and d.func.attr == "post"
        and d.args
        and isinstance(d.args[0], ast.Constant)
        and d.args[0].value == "/api/v1/agent/tasks"
        for d in decorators
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Depends"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "get_current_user"
        for node in ast.walk(route)
    )
    assert "ToolExecutor" in service_source
    assert "build_read_only_orchestrator" in service_source
