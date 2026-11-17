from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .compare import compare_runs
from .data import load_dataset, write_json
from .review import record_human_review
from .runner import evaluate

DEFAULT_ROOT = Path(__file__).resolve().parents[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LedgerGate deterministic AI evaluation and reliability harness"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Run a versioned deterministic evaluation dataset")
    run.add_argument("--dataset-dir", type=Path, default=DEFAULT_ROOT / "datasets" / "v1")
    run.add_argument("--output-dir", type=Path, default=DEFAULT_ROOT / "outputs" / "latest")
    run.add_argument("--model-version", default="not-specified")
    run.add_argument("--ledgergate-root", type=Path, default=None,
                     help="Optional actual LedgerGate checkout for backend adapter tests")
    run.add_argument("--trace-file", type=Path, default=None,
                     help="JSONL production agent telemetry/trace observations")
    run.add_argument("--postgres-url", default=os.getenv("EVAL_POSTGRES_URL") or os.getenv("TEST_POSTGRES_URL"))
    run.add_argument("--ci", action="store_true",
                     help="Fail on deterministic oracle or live backend contract regressions")

    validate = commands.add_parser("validate-dataset", help="Verify dataset manifest and JSONL inputs")
    validate.add_argument("--dataset-dir", type=Path, default=DEFAULT_ROOT / "datasets" / "v1")

    compare = commands.add_parser("compare", help="Compare two JSON benchmark run files")
    compare.add_argument("--baseline", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)

    review = commands.add_parser("review", help="Record a human decision for a queued case")
    review.add_argument("--queue", type=Path, required=True)
    review.add_argument("--case-id", required=True)
    review.add_argument("--reviewer-id", required=True)
    review.add_argument("--decision", choices=[
        "accepted", "rejected", "escalated", "needs_more_evidence"
    ], required=True)
    review.add_argument("--rationale", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate-dataset":
        manifest, data = load_dataset(args.dataset_dir)
        print(json.dumps({
            "dataset": manifest.get("name"), "version": manifest.get("dataset_version"),
            "case_counts": {key: len(value) for key, value in data.items()},
        }, indent=2))
        return 0
    if args.command == "run":
        result = evaluate(
            args.dataset_dir, args.output_dir, args.model_version,
            args.ledgergate_root, args.trace_file, args.postgres_url,
        )
        print(json.dumps({
            "run_id": result["run_id"], "model_version": result["model_version"],
            "output_dir": str(args.output_dir),
            "case_count": result["runner_performance"]["total_cases"],
            "model_serving_metrics": result["model_serving_metrics"],
            "live_ledgergate": result["metrics"]["live_ledgergate"],
        }, indent=2))
        if args.ci:
            metrics = result["metrics"]
            mandatory = [
                "financial_reference_oracle", "authorization_policy_fixtures",
                "failure_recovery_fixture", "multi_step_execution_fixture",
                "agent_task_completion_fixture",
            ]
            failed = [name for name in mandatory if metrics[name].get("accuracy") != 1.0]
            if args.ledgergate_root is not None:
                live = metrics["live_ledgergate"]
                agent = live["reconciliation_agent"]
                if agent.get("accuracy") != 1.0:
                    failed.append("live_reconciliation_agent")
                if agent.get("multi_step_trace", {}).get("accuracy") != 1.0:
                    failed.append("live_reconciliation_multi_step_trace")
                recovery = agent.get("safe_failure_recovery", {})
                if recovery.get("count", 0) and recovery.get("accuracy") != 1.0:
                    failed.append("live_reconciliation_safe_failure_recovery")
                contracts = live["backend_contracts"]
                for suite in ("tool_contracts", "retrieval_contracts"):
                    values = contracts[suite]
                    if values["count"] != values["passed"]:
                        failed.append("live_" + suite)
                pg = live["postgres_rls_inventory"]
                if pg.get("status") == "measured" and pg.get("count") != pg.get("passed"):
                    failed.append("postgres_rls_inventory")
            if failed:
                print("CI evaluation gates failed: " + ", ".join(failed), file=sys.stderr)
                return 1
        return 0
    if args.command == "compare":
        result = compare_runs(args.baseline, args.candidate)
        write_json(args.output, result)
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "review":
        path = record_human_review(
            args.queue, args.case_id, args.reviewer_id, args.decision, args.rationale
        )
        print(f"Review recorded in {path}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
