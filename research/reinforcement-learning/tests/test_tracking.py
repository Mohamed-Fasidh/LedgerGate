import json

from rl_research.tracking import ExperimentTracker


def test_experiment_tracker_writes_json_and_csv(tmp_path):
    tracker = ExperimentTracker(tmp_path, "tiny", {"seed": 3})
    tracker.log(step=1, reward=0.5)
    tracker.log(step=2, reward=0.75)
    tracker.close({"success": True})
    metrics = json.loads((tmp_path / "tiny" / "metrics.json").read_text())
    assert metrics["config"]["seed"] == 3
    assert len(metrics["history"]) == 2
    assert "reward" in (tmp_path / "tiny" / "metrics.csv").read_text()
