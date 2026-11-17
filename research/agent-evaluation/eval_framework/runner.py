from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from .data import load_dataset, read_jsonl, write_json
from .financial import calculate_invoice_total
from .integration import run_live_backend_contracts, run_live_reconciliation, run_postgres_rls_inventory
from .metrics import retrieval_metrics, summarize_durations_ms
from .retrieval import rank_documents
from .security import policy_allows


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(bool(row["passed"]) for row in rows)
    return {"count": len(rows), "passed": passed, "failed": len(rows) - passed,
            "accuracy": passed / len(rows) if rows else None}


def _load_telemetry(path: Path | None) -> list[dict[str, Any]]:
    return [] if path is None else read_jsonl(path, id_field="trace_id")


def _evaluate_traces(traces: list[dict[str, Any]]) -> dict[str, Any]:
    if not traces:
        return {"status": "not_measured", "records": 0,
                "reason": "No production agent trace file supplied"}
    for index, trace in enumerate(traces, 1):
        for key in ("latency_ms", "duration_ms", "started_at_ms", "completed_at_ms",
                    "input_tokens", "output_tokens"):
            if trace.get(key) is not None and float(trace[key]) < 0:
                raise ValueError(f"Trace {trace.get('trace_id', index)} has negative {key}")
        if trace.get("started_at_ms") is not None and trace.get("completed_at_ms") is not None:
            if float(trace["completed_at_ms"]) < float(trace["started_at_ms"]):
                raise ValueError(f"Trace {trace.get('trace_id', index)} completed before it started")
        for key in ("tool_calls", "claims", "recovery_expected", "recovery_observed",
                    "expected_steps", "observed_steps"):
            if key in trace and not isinstance(trace[key], list):
                raise ValueError(f"Trace {trace.get('trace_id', index)} field {key} must be a list")
    completed = [row for row in traces if "task_expected" in row and "task_completed" in row]
    tool_calls = [call for trace in traces for call in trace.get("tool_calls", [])]
    claims = [claim for trace in traces for claim in trace.get("claims", [])]
    schema_checks = [call for call in tool_calls if isinstance(call.get("schema_valid"), bool)]
    latencies = [float(row["latency_ms"]) for row in traces if row.get("latency_ms") is not None]
    input_tokens = sum(int(row.get("input_tokens", 0)) for row in traces)
    output_tokens = sum(int(row.get("output_tokens", 0)) for row in traces)
    durations = [float(row.get("duration_ms", row.get("latency_ms", 0))) for row in traces
                 if row.get("duration_ms", row.get("latency_ms")) is not None]
    started = [float(row["started_at_ms"]) for row in traces if row.get("started_at_ms") is not None]
    ended = [float(row["completed_at_ms"]) for row in traces if row.get("completed_at_ms") is not None]
    selection = (sum(call.get("expected_tool") == call.get("observed_tool") for call in tool_calls)
                 / len(tool_calls)) if tool_calls else None
    arguments = (sum(call.get("expected_arguments") == call.get("observed_arguments") for call in tool_calls)
                 / len(tool_calls)) if tool_calls else None
    claim_support = (
        sum(set(row.get("evidence_cited", [])) <= set(row.get("evidence_available", []))
            and bool(row.get("evidence_cited")) for row in claims) / len(claims)
    ) if claims else None
    wall_seconds = (max(ended) - min(started)) / 1000 if started and ended and max(ended) >= min(started) else None
    serial_duration_seconds = sum(durations) / 1000 if durations else None
    recovery_cases = [row for row in traces if "recovery_expected" in row and "recovery_observed" in row]
    multistep_cases = [row for row in traces if "expected_steps" in row and "observed_steps" in row]
    versions = sorted({str(row.get("model_version")) for row in traces if row.get("model_version")})
    return {
        "status": "measured", "records": len(traces), "observed_model_versions": versions,
        "task_completion": {
            "count": len(completed),
            "accuracy": sum(row["task_expected"] == row["task_completed"] for row in completed) / len(completed) if completed else None,
        },
        "tool_selection_accuracy": {"count": len(tool_calls), "accuracy": selection},
        "tool_argument_exact_match": {"count": len(tool_calls), "accuracy": arguments},
        "tool_argument_schema_validity": {
            "count": len(schema_checks),
            "rate": sum(row["schema_valid"] for row in schema_checks) / len(schema_checks) if schema_checks else None,
        },
        "claim_evidence_support": {"count": len(claims), "rate": claim_support},
        "latency": summarize_durations_ms(latencies),
        "tokens": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                   "total_tokens": input_tokens + output_tokens},
        "failure_recovery": {
            "count": len(recovery_cases),
            "accuracy": sum(row["recovery_expected"] == row["recovery_observed"] for row in recovery_cases) / len(recovery_cases) if recovery_cases else None,
        },
        "multi_step_execution": {
            "count": len(multistep_cases),
            "accuracy": sum(row["expected_steps"] == row["observed_steps"] for row in multistep_cases) / len(multistep_cases) if multistep_cases else None,
        },
        "throughput": {
            "requests_per_second_wall_clock": len(traces) / wall_seconds if wall_seconds and wall_seconds > 0 else None,
            "output_tokens_per_second_wall_clock": output_tokens / wall_seconds if wall_seconds and wall_seconds > 0 else None,
            "output_tokens_per_second_serial_request_time": output_tokens / serial_duration_seconds if serial_duration_seconds and serial_duration_seconds > 0 else None,
            "wall_clock_span_seconds": wall_seconds,
        },
        "timing_note": "Wall-clock throughput requires started_at_ms and completed_at_ms from one shared clock domain.",
    }


def evaluate(dataset_dir: Path, output_dir: Path, model_version: str = "not-specified",
             ledgergate_root: Path | None = None, trace_file: Path | None = None,
             postgres_url: str | None = None) -> dict[str, Any]:
    run_started = time.perf_counter()
    manifest, data = load_dataset(dataset_dir)
    metrics: dict[str, Any] = {}
    all_cases: list[dict[str, Any]] = []
    timings: dict[str, float] = {}

    def add_rows(suite: str, rows: list[dict[str, Any]]) -> None:
        all_cases.extend({"suite": suite, **row} for row in rows)

    start = time.perf_counter()
    agent_rows = [{
        "case_id": row["case_id"], "expected": row["expected_state"], "actual": row["observed_state"],
        "passed": row["expected_state"] == row["observed_state"],
        "detail": {"scenario": row["scenario"], "source": row.get("source"),
                   "path_exact": row["expected_path"] == row["observed_path"]},
    } for row in data["agent"]]
    metrics["agent_task_completion_fixture"] = {
        **_aggregate(agent_rows), "source": "scripted_golden_traces",
        "scope_note": "Scripted observations, not production LLM/agent accuracy.",
    }
    metrics["agent_state_path_exact_match_fixture"] = {
        "count": len(agent_rows),
        "accuracy": sum(row["detail"]["path_exact"] for row in agent_rows) / len(agent_rows) if agent_rows else None,
        "source": "scripted_golden_traces",
    }
    add_rows("agent_fixture", agent_rows)
    timings["agent_fixture_ms"] = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    tool_rows = []
    for row in data["tools"]:
        selected_ok = row["expected_tool"] == row["observed_tool"]
        args_ok = row["expected_arguments"] == row["observed_arguments"]
        tool_rows.append({
            "case_id": row["case_id"], "expected": row["expected_tool"], "actual": row["observed_tool"],
            "passed": selected_ok,
            "detail": {"intent": row["intent"], "arguments_exact": args_ok,
                       "expected_arguments": row["expected_arguments"],
                       "observed_arguments": row["observed_arguments"]},
        })
    metrics["tool_selection_accuracy_fixture"] = {
        **_aggregate(tool_rows), "source": "scripted_contract_traces",
        "scope_note": "Not a measurement of live model selection; use --trace-file for actual traces.",
    }
    arg_correct = sum(row["detail"]["arguments_exact"] for row in tool_rows)
    metrics["tool_argument_correctness_fixture"] = {
        "count": len(tool_rows), "passed": arg_correct,
        "accuracy": arg_correct / len(tool_rows) if tool_rows else None,
        "source": "scripted_contract_traces",
    }
    add_rows("tool_fixture", tool_rows)
    timings["tools_fixture_ms"] = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    retrieval_rows, retrieval_details = [], []
    for case in data["retrieval"]:
        ranked = rank_documents(case["query"], case["documents"])
        ids = [row["id"] for row in ranked]
        relevance = {row["id"]: int(row["relevance"]) for row in case["documents"]}
        score = retrieval_metrics(ids, relevance, int(case["k"]))
        minimum_recall = 0.5
        passed = score["recall_at_k"] is not None and score["recall_at_k"] >= minimum_recall
        retrieval_rows.append({
            "case_id": case["case_id"], "expected": f"recall_at_{case['k']}>={minimum_recall}",
            "actual": score["recall_at_k"], "passed": passed,
            "detail": {"query": case["query"], "ranked_ids": ids[:int(case["k"])], **score},
        })
        retrieval_details.append(score)
    n_retrieval = len(retrieval_details)
    metrics["retrieval"] = {
        "count": n_retrieval,
        "mean_precision_at_k": mean(row["precision_at_k"] for row in retrieval_details) if n_retrieval else None,
        "mean_recall_at_k": mean(row["recall_at_k"] for row in retrieval_details) if n_retrieval else None,
        "mean_mrr": mean(row["reciprocal_rank"] for row in retrieval_details) if n_retrieval else None,
        "mean_ndcg_at_k": mean(row["ndcg_at_k"] for row in retrieval_details) if n_retrieval else None,
        "cases_passed_recall_gate": sum(row["passed"] for row in retrieval_rows),
        "source": "offline_hybrid_scorer_mirror", "k": "per-case from dataset",
        "scope_note": "Synthetic corpus; live SQL retrieval is measured separately with --ledgergate-root.",
    }
    add_rows("retrieval_fixture", retrieval_rows)
    timings["retrieval_fixture_ms"] = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    claim_rows = []
    for row in data["claims"]:
        cited, available = set(row.get("evidence_cited", [])), set(row.get("evidence_available", []))
        supported = bool(cited) and cited.issubset(available)
        claim_rows.append({
            "case_id": row["case_id"], "expected": "all cited evidence IDs exist",
            "actual": "supported" if supported else "unsupported", "passed": supported,
            "detail": {"claim": row["claim"], "missing_evidence_ids": sorted(cited - available)},
        })
    unsupported = sum(not row["passed"] for row in claim_rows)
    metrics["claim_evidence_integrity"] = {
        "count": len(claim_rows), "supported": len(claim_rows) - unsupported,
        "unsupported_or_uncited": unsupported,
        "unsupported_reference_rate": unsupported / len(claim_rows) if claim_rows else None,
        "source": "deterministic_evidence_reference_check",
        "scope_note": "Citation-ID integrity proxy only; not semantic entailment or a complete hallucination rate.",
    }
    add_rows("claim_evidence", claim_rows)
    timings["claims_ms"] = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    recovery_rows = [{
        "case_id": row["case_id"], "expected": row["expected_events"], "actual": row["observed_events"],
        "passed": row["expected_recovery"] is not None and row["expected_events"] == row["observed_events"],
        "detail": {"failure": row["failure"], "expected_recovery": row["expected_recovery"]},
    } for row in data["recovery"]]
    metrics["failure_recovery_fixture"] = {
        **_aggregate(recovery_rows), "source": "scripted_failure_traces",
        "scope_note": "Golden sequence conformance only; live fault injection is separate.",
    }
    add_rows("failure_recovery_fixture", recovery_rows)
    timings["recovery_ms"] = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    multistep_rows = [{
        "case_id": row["case_id"], "expected": row["expected_steps"], "actual": row["observed_steps"],
        "passed": row["expected_steps"] == row["observed_steps"], "detail": {"task": row["task"]},
    } for row in data["multistep"]]
    metrics["multi_step_execution_fixture"] = {**_aggregate(multistep_rows), "source": "scripted_step_traces"}
    add_rows("multistep_fixture", multistep_rows)
    timings["multistep_ms"] = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    financial_rows = []
    for row in data["financial"]:
        actual = calculate_invoice_total(row)
        expected = {"subtotal": row["expected_subtotal"], "tax": row["expected_tax"],
                    "total": row["expected_total"]}
        financial_rows.append({"case_id": row["case_id"], "expected": expected, "actual": actual,
                               "passed": actual == expected, "detail": {"description": row["description"]}})
    metrics["financial_reference_oracle"] = {
        **_aggregate(financial_rows), "source": "Decimal_HALF_UP_reference_oracle",
        "scope_note": "Reference arithmetic oracle; not a line-item API currently implemented by LedgerGate.",
    }
    add_rows("financial_oracle", financial_rows)
    timings["financial_ms"] = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    security_rows = []
    for row in data["security"]:
        allowed = policy_allows(row["tenant_id"], row["resource_tenant_id"], row["role"], row["action"])
        security_rows.append({
            "case_id": row["case_id"], "expected": row["expected_allow"], "actual": allowed,
            "passed": allowed == row["expected_allow"],
            "detail": {"role": row["role"], "action": row["action"],
                       "tenant_match": row["tenant_id"] == row["resource_tenant_id"]},
        })
    metrics["authorization_policy_fixtures"] = {
        **_aggregate(security_rows), "source": "versioned_policy_contract_fixture",
        "scope_note": "Reference role/tenant policy only; not a substitute for route and PostgreSQL RLS tests.",
    }
    add_rows("security_fixture", security_rows)
    timings["security_ms"] = (time.perf_counter() - start) * 1000

    if ledgergate_root is not None:
        live_start = time.perf_counter()
        live = run_live_reconciliation(ledgergate_root, data["live_reconciliation"])
        contracts = run_live_backend_contracts(ledgergate_root, data["retrieval"])
        pg = run_postgres_rls_inventory(ledgergate_root, postgres_url)
        metrics["live_ledgergate"] = {
            "reconciliation_agent": live, "backend_contracts": contracts,
            "postgres_rls_inventory": pg,
        }
        for row in live["cases"]:
            add_rows("live_reconciliation", [{
                "case_id": row["case_id"], "expected": row["expected"], "actual": row["actual"],
                "passed": row["passed"], "detail": {"trace": row["trace"], "findings": row["findings"]},
            }])
            add_rows("live_multi_step_path", [{
                "case_id": row["case_id"], "expected": "exact expected state path",
                "actual": row["trace"], "passed": row["trace_valid"],
                "detail": {"scenario": row["scenario"]},
            }])
            if row["actual"] == "failed":
                add_rows("live_failure_recovery", [{
                    "case_id": row["case_id"], "expected": "redacted fail-closed finding",
                    "actual": row["findings"], "passed": row["safe_failure"],
                    "detail": {"scenario": row["scenario"]},
                }])
        for kind in ("tool_contracts", "retrieval_contracts"):
            for row in contracts[kind]["cases"]:
                add_rows("live_" + kind, [{
                    "case_id": row["case_id"], "expected": "contract satisfied",
                    "actual": row.get("actual", row.get("returned")), "passed": row["passed"],
                    "detail": row,
                }])
        for row in contracts["retrieval_benchmark"]["cases"]:
            add_rows("live_retrieval_benchmark", [{
                "case_id": row["case_id"], "expected": "recall_at_k >= 0.5",
                "actual": row["recall_at_k"], "passed": row["passed"], "detail": row,
            }])
        timings["live_integration_ms"] = (time.perf_counter() - live_start) * 1000
    else:
        metrics["live_ledgergate"] = {
            "status": "not_measured", "reason": "No local LedgerGate checkout passed with --ledgergate-root"
        }

    traces = _load_telemetry(trace_file)
    metrics["observed_agent_traces"] = _evaluate_traces(traces)
    elapsed_ms = (time.perf_counter() - run_started) * 1000
    total_cases = len(all_cases)
    module_root = dataset_dir.resolve().parents[1]
    config_path = module_root / "configs" / "default.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest() if config_path.exists() else None
    git_sha = None
    if ledgergate_root is not None and (ledgergate_root / ".git").exists():
        try:
            git_sha = subprocess.run(
                ["git", "-C", str(ledgergate_root), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            git_sha = None

    run = {
        "schema_version": "1.0.0",
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ"),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dataset": {"name": manifest["name"], "version": manifest["dataset_version"],
                    "manifest_sha256": manifest.get("manifest_sha256"),
                    "case_counts": {suite: len(rows) for suite, rows in data.items()}},
        "model_version": model_version,
        "environment": {
            "python": platform.python_version(), "platform": platform.platform(),
            "implementation": platform.python_implementation(), "machine": platform.machine(),
            "processor": platform.processor(), "cpu_count": os.cpu_count(),
            "ledgergate_root_supplied": ledgergate_root is not None,
            "postgres_url_supplied": bool(postgres_url), "trace_file_supplied": trace_file is not None,
            "git_commit": git_sha,
        },
        "configuration": {"values": config, "sha256": config_hash},
        "metrics": metrics,
        "runner_performance": {
            "total_cases": total_cases, "dataset_cases_total": sum(len(rows) for rows in data.values()),
            "offline_fixture_cases": sum(len(rows) for key, rows in data.items() if key != "live_reconciliation"),
            "live_adapter_input_cases": len(data.get("live_reconciliation", [])),
            "live_adapter_cases_executed": len(data.get("live_reconciliation", [])) if ledgergate_root is not None else 0,
            "evaluation_runtime_ms": elapsed_ms,
            "evaluation_cases_per_second": total_cases / (elapsed_ms / 1000) if elapsed_ms > 0 else None,
            "suite_runtime_ms": timings,
            "note": "Evaluator execution speed, not model inference latency or serving throughput.",
        },
        "model_serving_metrics": {
            "status": "not_measured" if not traces else "see_observed_agent_traces",
            "latency_ms": None if not traces else metrics["observed_agent_traces"]["latency"],
            "token_counts": None if not traces else metrics["observed_agent_traces"]["tokens"],
            "throughput": None if not traces else metrics["observed_agent_traces"]["throughput"],
            "reason": "No production agent telemetry is bundled. Supply real trace records; do not infer serving performance from fixture runtime."
                if not traces else None,
        },
        "case_results": all_cases,
        "limitations": [
            "Scripted fixtures validate evaluator behavior, not production LLM-agent accuracy.",
            "No production model outputs, token counts, inference timings or concurrency telemetry are included by default.",
            "Claim support measures evidence-reference integrity, not semantic entailment or complete hallucination rate.",
            "Financial arithmetic is a Decimal reference oracle, not an existing LedgerGate line-item endpoint.",
            "SQLite integration verifies ORM tenant filters; only PostgreSQL-backed CI exercises RLS and runtime-role grants.",
            "LLM-as-judge is supplementary and does not replace deterministic checks or human review.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "run.json", run)
    _write_case_csv(output_dir / "case_results.csv", all_cases)
    _write_failure_analysis(output_dir / "failure_analysis.json", all_cases)
    _write_review_queue(output_dir / "human_review_queue.jsonl", all_cases)
    (output_dir / "benchmark_report.md").write_text(render_report(run), encoding="utf-8")
    return run


def _write_case_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["suite", "case_id", "expected", "actual", "passed", "detail_json"])
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "suite": row.get("suite"), "case_id": row.get("case_id"),
                "expected": json.dumps(row.get("expected"), ensure_ascii=False),
                "actual": json.dumps(row.get("actual"), ensure_ascii=False),
                "passed": row.get("passed"),
                "detail_json": json.dumps(row.get("detail", {}), sort_keys=True, ensure_ascii=False),
            })


def _write_failure_analysis(path: Path, rows: list[dict[str, Any]]) -> None:
    failed = [row for row in rows if not row.get("passed")]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in failed:
        grouped.setdefault(row["suite"], []).append(row)
    argument_failures = [
        row for row in rows if row.get("suite") == "tool_fixture"
        and row.get("detail", {}).get("arguments_exact") is False
    ]
    if argument_failures:
        grouped["tool_argument_mismatch"] = argument_failures
    write_json(path, {
        "failure_count": len(failed) + len(argument_failures),
        "case_level_failures": len(failed),
        "additional_argument_failures": len(argument_failures),
        "by_suite": grouped, "failure_modes": sorted(grouped),
    })


def _write_review_queue(path: Path, rows: list[dict[str, Any]]) -> None:
    candidates = []
    for row in rows:
        detail = row.get("detail", {})
        needs_review = (
            not row.get("passed")
            or (row.get("suite") == "claim_evidence" and row.get("actual") == "unsupported")
            or (row.get("suite") == "tool_fixture" and detail.get("arguments_exact") is False)
        )
        if needs_review:
            candidates.append({
                "case_id": row["case_id"], "suite": row["suite"],
                "reason": "deterministic_mismatch_or_unsupported_evidence",
                "expected": row.get("expected"), "actual": row.get("actual"),
                "review_status": "pending",
            })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in candidates),
        encoding="utf-8",
    )


def render_report(run: dict[str, Any]) -> str:
    metrics = run["metrics"]
    lines = [
        f"# LedgerGate Evaluation Report — {run['run_id']}", "",
        f"- Dataset: `{run['dataset']['name']}` v`{run['dataset']['version']}`",
        f"- Model version: `{run['model_version']}`",
        f"- Python: `{run['environment']['python']}`",
        f"- Dataset records: {run['runner_performance']['dataset_cases_total']} total; "
        f"{run['runner_performance']['offline_fixture_cases']} offline; "
        f"{run['runner_performance']['live_adapter_input_cases']} reserved for live adapter.",
        "",
        "> Fixture scores are scoped to their source. They are not production LLM-agent accuracy.", "",
        "## Deterministic dataset results", "",
        "| Area | Result | Scope |", "|---|---:|---|",
    ]

    def ratio(value: dict) -> str:
        accuracy = value.get("accuracy")
        pct = 0.0 if accuracy is None else accuracy * 100
        return f"{value.get('passed', 0)}/{value.get('count', 0)} ({pct:.1f}%)"

    lines.extend([
        f"| Agent task-state golden traces | {ratio(metrics['agent_task_completion_fixture'])} | Scripted traces |",
        f"| Tool selection golden traces | {ratio(metrics['tool_selection_accuracy_fixture'])} | Scripted tool traces |",
        f"| Tool argument exact match | {metrics['tool_argument_correctness_fixture']['passed']}/{metrics['tool_argument_correctness_fixture']['count']} ({(metrics['tool_argument_correctness_fixture']['accuracy'] or 0)*100:.1f}%) | Scripted traces |",
        f"| Retrieval mean precision@k | {metrics['retrieval']['mean_precision_at_k']:.3f} | Synthetic v1 corpus |",
        f"| Retrieval mean recall@k | {metrics['retrieval']['mean_recall_at_k']:.3f} | Synthetic v1 corpus |",
        f"| Citation-reference support | {metrics['claim_evidence_integrity']['supported']}/{metrics['claim_evidence_integrity']['count']} | Reference integrity proxy |",
        f"| Failure recovery trace conformance | {ratio(metrics['failure_recovery_fixture'])} | Scripted sequences |",
        f"| Multi-step trace exactness | {ratio(metrics['multi_step_execution_fixture'])} | Scripted step sequences |",
        f"| Financial Decimal oracle | {ratio(metrics['financial_reference_oracle'])} | Reference arithmetic cases |",
        f"| Authorization policy fixtures | {ratio(metrics['authorization_policy_fixtures'])} | Reference policy matrix |",
    ])
    live = metrics["live_ledgergate"]
    if live.get("status") == "not_measured":
        lines.extend([
            "", "## Live LedgerGate integration", "",
            "Not measured in this run: no local checkout was supplied. Use --ledgergate-root to execute the actual backend adapter.",
        ])
    else:
        reconciliation = live["reconciliation_agent"]
        tools = live["backend_contracts"]["tool_contracts"]
        retrieval = live["backend_contracts"]["retrieval_contracts"]
        benchmark = live["backend_contracts"]["retrieval_benchmark"]
        pg = live["postgres_rls_inventory"]
        lines.extend([
            "", "## Live LedgerGate integration", "",
            f"- Reconciliation agent: {reconciliation['passed']}/{reconciliation['count']} ({(reconciliation['accuracy'] or 0)*100:.1f}%)",
            f"- State trace checks: {reconciliation['multi_step_trace']['passed']}/{reconciliation['multi_step_trace']['count']}",
            f"- Safe-failure checks: {reconciliation['safe_failure_recovery']['passed']}/{reconciliation['safe_failure_recovery']['count']}",
            f"- Tool executor contracts: {tools['passed']}/{tools['count']}",
            f"- SQL retrieval scope contracts: {retrieval['passed']}/{retrieval['count']}",
            f"- Actual SQL retrieval mean P@k/R@k: {benchmark['mean_precision_at_k']:.3f}/{benchmark['mean_recall_at_k']:.3f}",
            f"- PostgreSQL RLS inventory: {pg.get('status')} ({pg.get('passed', 'n/a')}/{pg.get('count', 'n/a')})",
        ])
    serving = run["model_serving_metrics"]
    lines.extend([
        "", "## Model serving performance", "",
        f"Status: **{serving['status']}**. {serving['reason'] or 'Metrics were computed from supplied actual traces.'}",
        "", "## Evaluator performance", "",
        f"- Cases scored: {run['runner_performance']['total_cases']}",
        f"- Runtime: {run['runner_performance']['evaluation_runtime_ms']:.2f} ms",
        f"- Evaluator throughput: {run['runner_performance']['evaluation_cases_per_second']:.1f} cases/s (not model throughput)",
        "", "## Limitations", "",
    ])
    lines.extend(f"- {item}" for item in run["limitations"])
    return "\n".join(lines) + "\n"
