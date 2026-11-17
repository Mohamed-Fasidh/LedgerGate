"""Authenticated read-only agent task execution service."""
from app.agent.orchestrator import AgentRunResult, TaskOrchestrator
from app.agent.planner import TaskPlanner
from app.agent.tool_executor import ToolExecutor
from app.services.inference import InferenceClient


def run_read_only_task(
    db,
    tenant_id: str,
    task: str,
    *,
    inference_client: InferenceClient | None = None,
    max_steps: int = 5,
    timeout_seconds: float = 30.0,
) -> AgentRunResult:
    """Plan and execute a bounded read-only task under one fixed tenant context."""
    executor = ToolExecutor(db, tenant_id)
    client = inference_client or InferenceClient()
    planner = TaskPlanner(executor, client, max_steps=max_steps)
    orchestrator = TaskOrchestrator(
        planner,
        executor,
        max_steps=max_steps,
        timeout_seconds=timeout_seconds,
    )
    return orchestrator.run(task)
