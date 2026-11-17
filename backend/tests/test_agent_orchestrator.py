from threading import Event

from app.agent.orchestrator import TaskOrchestrator
from app.agent.planner import PlannedStep, TaskPlan, PlanningError


class FakePlanner:
    def __init__(self, plan=None, error=None):
        self.plan_result = plan
        self.error = error

    def plan(self, task, context=None):
        if self.error:
            raise self.error
        return self.plan_result


class FakeExecutor:
    def __init__(self, results=None, error=None):
        self.results = list(results or [])
        self.error = error
        self.calls = []

    def execute(self, tool, arguments):
        self.calls.append((tool, arguments))
        if self.error:
            raise self.error
        return self.results.pop(0) if self.results else {"ok": True}


def make_plan(*steps):
    return TaskPlan(
        goal="Gather evidence",
        steps=[
            PlannedStep(tool=tool, arguments=arguments, purpose="Read evidence")
            for tool, arguments in steps
        ],
    )


def test_orchestrator_executes_validated_read_only_steps_and_traces_them():
    plan = make_plan(
        ("invoice_lookup", {"invoice_id": 10}),
        ("policy_search", {"query": "duplicate invoice"}),
    )
    executor = FakeExecutor([{"invoice_number": "INV-10"}, {"matches": []}])
    result = TaskOrchestrator(FakePlanner(plan), executor).run("Review invoice 10")

    assert result.status == "completed"
    assert len(result.outputs) == 2
    assert [row["event"] for row in result.trace].count("step_succeeded") == 2
    assert executor.calls[0] == ("invoice_lookup", {"invoice_id": 10})


def test_orchestrator_stops_when_cancelled_between_steps():
    plan = make_plan(
        ("invoice_lookup", {"invoice_id": 10}),
        ("policy_search", {"query": "duplicate invoice"}),
    )
    event = Event()
    event.set()
    executor = FakeExecutor()
    result = TaskOrchestrator(FakePlanner(plan), executor).run(
        "Review invoice", cancel_event=event
    )
    assert result.status == "cancelled"
    assert result.error_code == "CANCELLED"
    assert executor.calls == []


def test_orchestrator_enforces_deadline_before_tool_execution():
    plan = make_plan(("invoice_lookup", {"invoice_id": 10}))
    executor = FakeExecutor()
    result = TaskOrchestrator(
        FakePlanner(plan), executor, timeout_seconds=0.0000001
    ).run("Review invoice")
    assert result.status == "timed_out"
    assert result.error_code == "EXECUTION_DEADLINE_EXCEEDED"
    assert executor.calls == []


def test_orchestrator_redacts_tool_failure_and_stops_following_steps():
    plan = make_plan(
        ("invoice_lookup", {"invoice_id": 10}),
        ("policy_search", {"query": "duplicate invoice"}),
    )
    executor = FakeExecutor(error=RuntimeError("database password=secret"))
    result = TaskOrchestrator(FakePlanner(plan), executor).run("Review invoice")
    assert result.status == "failed"
    assert result.error_code == "TOOL_EXECUTION_FAILED"
    assert len(executor.calls) == 1
    assert "secret" not in str(result.trace)


def test_orchestrator_redacts_planning_failure():
    result = TaskOrchestrator(
        FakePlanner(error=PlanningError("provider secret")),
        FakeExecutor(),
    ).run("Review invoice")
    assert result.status == "failed"
    assert result.error_code == "PLAN_FAILED"
    assert "provider secret" not in str(result.trace)


def test_orchestrator_rejects_plan_that_exceeds_its_step_budget():
    plan = make_plan(
        ("invoice_lookup", {"invoice_id": 1}),
        ("invoice_lookup", {"invoice_id": 2}),
    )
    executor = FakeExecutor()
    result = TaskOrchestrator(
        FakePlanner(plan), executor, max_steps=1
    ).run("Review invoices")
    assert result.status == "failed"
    assert result.error_code == "STEP_LIMIT_EXCEEDED"
    assert executor.calls == []
