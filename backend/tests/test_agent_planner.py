import json
from types import SimpleNamespace

import pytest

from app.agent.planner import PlanningError, TaskPlanner
from app.agent.tool_executor import InvoiceLookup, PolicySearch
from app.services.inference import InferenceError


class FakeExecutor:
    registry = {
        "invoice_lookup": (InvoiceLookup, None),
        "policy_search": (PolicySearch, None),
    }

    def execute(self, *args, **kwargs):
        raise AssertionError("Planning must never execute a tool")


class FakeInferenceClient:
    def __init__(self, text=None, error=None):
        self.text = text
        self.error = error
        self.messages = None
        self.max_tokens = None

    def generate(self, messages, *, max_tokens=None):
        self.messages = messages
        self.max_tokens = max_tokens
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text)


def planner_for(text=None, error=None, **limits):
    client = FakeInferenceClient(text=text, error=error)
    return TaskPlanner(FakeExecutor(), client, **limits), client


def test_planner_returns_validated_allowlisted_plan_without_execution():
    raw = json.dumps({
        "goal": "Gather invoice evidence",
        "steps": [
            {
                "tool": "invoice_lookup",
                "arguments": {"invoice_id": "42"},
                "purpose": "Read the invoice",
            }
        ],
    })
    planner, client = planner_for(raw)
    plan = planner.plan("Review invoice 42", {"authenticated_tenant": "tenant-a"})
    assert plan.steps[0].tool == "invoice_lookup"
    assert plan.steps[0].arguments == {"invoice_id": 42}
    assert client.max_tokens == 1000
    assert client.messages[0]["role"] == "system"
    assert client.messages[1]["role"] == "user"


def test_planner_rejects_non_allowlisted_tool():
    planner, _ = planner_for(json.dumps({
        "goal": "Run an unsafe action",
        "steps": [{"tool": "shell", "arguments": {}, "purpose": "execute"}],
    }))
    with pytest.raises(PlanningError, match="not allowlisted"):
        planner.plan("Do something")


def test_planner_rejects_tool_arguments_with_tenant_override():
    planner, _ = planner_for(json.dumps({
        "goal": "Read invoice",
        "steps": [{
            "tool": "invoice_lookup",
            "arguments": {"invoice_id": 42, "tenant_id": "tenant-b"},
            "purpose": "Read invoice",
        }],
    }))
    with pytest.raises(PlanningError, match="invalid tool arguments"):
        planner.plan("Read invoice")


def test_planner_rejects_malformed_json_and_oversized_plans():
    malformed, _ = planner_for("not-json")
    with pytest.raises(PlanningError, match="invalid structured plan"):
        malformed.plan("Review invoice")

    too_many = json.dumps({
        "goal": "Read many invoices",
        "steps": [
            {"tool": "invoice_lookup", "arguments": {"invoice_id": index + 1}, "purpose": "Read"}
            for index in range(3)
        ],
    })
    limited, _ = planner_for(too_many, max_steps=2)
    with pytest.raises(PlanningError, match="maximum step count"):
        limited.plan("Review invoices")


def test_planner_rejects_oversized_task_and_non_json_context():
    planner, _ = planner_for("{}", max_task_chars=4)
    with pytest.raises(PlanningError, match="exceeds the planner limit"):
        planner.plan("A task that is too long")

    planner, _ = planner_for("{}")
    with pytest.raises(PlanningError, match="JSON-safe"):
        planner.plan("Review", {"bad": object()})


def test_planner_provider_failure_is_redacted():
    planner, _ = planner_for(error=InferenceError("provider secret"))
    with pytest.raises(PlanningError, match="provider is unavailable") as exc:
        planner.plan("Review invoice")
    assert "provider secret" not in str(exc.value)


def test_planner_does_not_accept_extra_top_level_fields():
    planner, _ = planner_for(json.dumps({
        "goal": "Read invoice",
        "steps": [{"tool": "invoice_lookup", "arguments": {"invoice_id": 1}, "purpose": "Read"}],
        "execute_now": True,
    }))
    with pytest.raises(PlanningError, match="invalid structured plan"):
        planner.plan("Review invoice")
