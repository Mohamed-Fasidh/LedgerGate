from __future__ import annotations

import json
from pathlib import Path
from typing import Any

METRIC_PATHS: list[tuple[str, ...]] = [
    ("agent_task_completion_fixture", "accuracy"),
    ("tool_selection_accuracy_fixture", "accuracy"),
    ("tool_argument_correctness_fixture", "accuracy"),
    ("retrieval", "mean_precision_at_k"),
    ("retrieval", "mean_recall_at_k"),
    ("retrieval", "mean_mrr"),
    ("retrieval", "mean_ndcg_at_k"),
    ("claim_evidence_integrity", "unsupported_reference_rate"),
    ("failure_recovery_fixture", "accuracy"),
    ("multi_step_execution_fixture", "accuracy"),
    ("financial_reference_oracle", "accuracy"),
    ("authorization_policy_fixtures", "accuracy"),
    ("observed_agent_traces", "task_completion", "accuracy"),
    ("observed_agent_traces", "tool_selection_accuracy", "accuracy"),
    ("observed_agent_traces", "tool_argument_exact_match", "accuracy"),
    ("observed_agent_traces", "tool_argument_schema_validity", "rate"),
    ("observed_agent_traces", "claim_evidence_support", "rate"),
    ("observed_agent_traces", "latency", "p95_ms"),
    ("observed_agent_traces", "tokens", "total_tokens"),
    ("observed_agent_traces", "throughput", "requests_per_second_wall_clock"),
    ("observed_agent_traces", "throughput", "output_tokens_per_second_wall_clock"),
]


def _metric(run: dict[str, Any], path: tuple[str, ...]) -> float | None:
    value: Any = run.get("metrics", {})
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def compare_runs(baseline_path: Path, candidate_path: Path) -> dict[str, Any]:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    same_dataset = baseline.get("dataset", {}).get("version") == candidate.get("dataset", {}).get("version")
    same_config = baseline.get("configuration", {}).get("sha256") == candidate.get("configuration", {}).get("sha256")
    rows = []
    for path in METRIC_PATHS:
        before, after = _metric(baseline, path), _metric(candidate, path)
        if before is None or after is None:
            status = "not_measured_in_both_runs"
        elif not same_dataset:
            status = "dataset_version_mismatch_diagnostic_only"
        elif not same_config:
            status = "configuration_changed_diagnostic_only"
        else:
            status = "compared"
        rows.append({
            "metric": ".".join(path), "baseline": before, "candidate": after,
            "delta": round(after - before, 12) if before is not None and after is not None else None,
            "status": status,
        })
    return {
        "baseline_model_version": baseline.get("model_version"),
        "candidate_model_version": candidate.get("model_version"),
        "baseline_run_id": baseline.get("run_id"), "candidate_run_id": candidate.get("run_id"),
        "dataset_versions": [baseline.get("dataset", {}).get("version"), candidate.get("dataset", {}).get("version")],
        "same_dataset_version": same_dataset, "same_configuration_hash": same_config,
        "metrics": rows,
        "warning": "Compare only runs with compatible dataset versions, configurations, filters and scoring implementations.",
    }
