from pathlib import Path

from eval_framework.data import load_dataset
from eval_framework.runner import evaluate


DATASET = Path(__file__).resolve().parents[1] / "datasets" / "v1"


def test_default_versioned_dataset_loads_all_required_suites():
    manifest, data = load_dataset(DATASET)
    assert manifest["dataset_version"] == "1.0.0"
    assert {"agent", "tools", "retrieval", "claims", "recovery",
            "multistep", "financial", "security"} <= set(data)
    assert all(data[name] for name in (
        "agent", "tools", "retrieval", "claims", "recovery",
        "multistep", "financial", "security"
    ))


def test_default_dataset_runs_and_exports_outputs(tmp_path):
    result = evaluate(DATASET, tmp_path, model_version="fixture-only")
    assert result["dataset"]["version"] == "1.0.0"
    assert result["metrics"]["agent_task_completion_fixture"]["accuracy"] == 1.0
    assert result["metrics"]["tool_selection_accuracy_fixture"]["accuracy"] == 1.0
    assert result["metrics"]["tool_argument_correctness_fixture"]["accuracy"] == 1.0
    assert result["metrics"]["financial_reference_oracle"]["accuracy"] == 1.0
    assert result["metrics"]["authorization_policy_fixtures"]["accuracy"] == 1.0
    assert result["model_serving_metrics"]["status"] == "not_measured"
    assert (tmp_path / "run.json").is_file()
    assert (tmp_path / "case_results.csv").is_file()
    assert (tmp_path / "failure_analysis.json").is_file()
    assert (tmp_path / "human_review_queue.jsonl").is_file()
    assert (tmp_path / "benchmark_report.md").is_file()
