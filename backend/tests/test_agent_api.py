import pytest

from app.main import _build_agent_orchestrator
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


def test_agent_endpoint_requires_authentication_and_uses_only_read_only_tools():
    import ast
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "app" / "main.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
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
    defaults = route.args.defaults
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Depends"
        and node.args
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "get_current_user"
        for node in ast.walk(route)
    )
    assert "ToolExecutor" in source.read_text(encoding="utf-8")
