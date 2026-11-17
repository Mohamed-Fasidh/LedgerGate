"""Bounded execution loop for validated, read-only agent plans.

This is an in-process coordinator, not durable orchestration. Tool handlers are
synchronous; deadlines and cancellation are cooperative between tool calls and
cannot interrupt a blocking database call already in progress.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from threading import Event
from typing import Any

from app.agent.planner import PlanningError, TaskPlan, TaskPlanner
from app.agent.tool_executor import READ_ONLY_TOOL_NAMES, ToolExecutor


@dataclass
class AgentRunResult:
    status: str
    plan: TaskPlan | None
    outputs: list[dict[str, Any]] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: float = 0.0
    error_code: str | None = None


class TaskOrchestrator:
    """Execute a finite plan sequentially using the executor's fixed tenant scope."""

    def __init__(
        self,
        planner: TaskPlanner,
        executor: ToolExecutor,
        *,
        max_steps: int = 5,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not 1 <= max_steps <= 10:
            raise ValueError("max_steps must be between 1 and 10")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if getattr(planner, "tool_executor", None) is not executor:
            raise ValueError("planner and orchestrator must share the same ToolExecutor instance")
        self.planner = planner
        self.executor = executor
        self.max_steps = max_steps
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        task: str,
        context: dict[str, Any] | None = None,
        *,
        cancel_event: Event | None = None,
    ) -> AgentRunResult:
        started = time.monotonic()
        trace: list[dict[str, Any]] = [{"event": "planning_started"}]
        outputs: list[dict[str, Any]] = []
        try:
            plan = self.planner.plan(task, context)
        except PlanningError:
            trace.append({"event": "planning_failed", "error_code": "PLAN_FAILED"})
            return self._result("failed", None, outputs, trace, started, "PLAN_FAILED")

        trace.append({"event": "planning_completed", "step_count": len(plan.steps)})
        if len(plan.steps) > self.max_steps:
            trace.append({"event": "step_limit_exceeded"})
            return self._result(
                "failed", plan, outputs, trace, started, "STEP_LIMIT_EXCEEDED"
            )

        status = "completed"
        error_code = None
        for index, step in enumerate(plan.steps, start=1):
            if cancel_event is not None and cancel_event.is_set():
                status, error_code = "cancelled", "CANCELLED"
                trace.append({"event": "cancelled", "next_step": index})
                break
            if time.monotonic() - started >= self.timeout_seconds:
                status, error_code = "timed_out", "EXECUTION_DEADLINE_EXCEEDED"
                trace.append({"event": "deadline_exceeded", "next_step": index})
                break
            if step.tool not in READ_ONLY_TOOL_NAMES:
                status, error_code = "failed", "TOOL_NOT_READ_ONLY"
                trace.append({
                    "event": "step_rejected",
                    "step": index,
                    "error_code": error_code,
                })
                break

            step_started = time.monotonic()
            trace.append({"event": "step_started", "step": index, "tool": step.tool})
            try:
                output = self.executor.execute(step.tool, step.arguments)
            except Exception:
                # Do not return tool exceptions, arguments, or provider data in traces.
                status, error_code = "failed", "TOOL_EXECUTION_FAILED"
                trace.append({
                    "event": "step_failed",
                    "step": index,
                    "tool": step.tool,
                    "error_code": error_code,
                })
                break

            outputs.append({"step": index, "tool": step.tool, "output": output})
            trace.append({
                "event": "step_succeeded",
                "step": index,
                "tool": step.tool,
                "duration_ms": round((time.monotonic() - step_started) * 1000, 3),
            })

        trace.append({"event": "run_finished", "status": status})
        return self._result(status, plan, outputs, trace, started, error_code)

    @staticmethod
    def _result(status, plan, outputs, trace, started, error_code):
        return AgentRunResult(
            status=status,
            plan=plan,
            outputs=outputs,
            trace=trace,
            duration_ms=round((time.monotonic() - started) * 1000, 3),
            error_code=error_code,
        )
