import asyncio
import csv
import json

from app.services.inference.benchmark import _token_count


def test_fallback_token_counter_is_explicitly_approximate(monkeypatch):
    monkeypatch.delenv("BENCHMARK_TOKENIZER", raising=False)
    count, source = _token_count("one two three")
    assert count == 3 and source == "whitespace_estimate"


def test_benchmark_writes_machine_readable_results_from_mock_server(monkeypatch, tmp_path):
    from app.services.inference import benchmark
    from app.services.inference.config import InferenceConfig

    class FakeClient:
        def __init__(self, config):
            self.config = config
        def health(self):
            return {"healthy": True, "model_available": True, "server_version": "test-only"}
        def stream(self, messages):
            yield "one "
            yield "two "
            yield "three"

    monkeypatch.setattr(benchmark, "InferenceClient", FakeClient)
    monkeypatch.setattr(benchmark, "_resource_snapshot", lambda: {"process_rss_bytes": 1234})
    monkeypatch.setattr(benchmark, "hardware_snapshot",
                        lambda: {"cuda_available": False, "gpu_utilization_percent": None})
    monkeypatch.setattr(benchmark, "software_snapshot", lambda: {"python": "test"})
    config = InferenceConfig(
        provider="vllm", base_url="http://test/v1", model="test", max_concurrent_requests=2
    )
    result = asyncio.run(benchmark.run_benchmark(
        config=config, concurrency=2, requests=3, output_dir=str(tmp_path), label="mock_test"
    ))
    assert result["successful_requests"] == 3
    assert result["failed_requests"] == 0
    assert result["server_version"] == "test-only"
    json_files = list(tmp_path.glob("*.json"))
    csv_files = list(tmp_path.glob("*.csv"))
    assert len(json_files) == 1 and len(csv_files) == 1
    payload = json.loads(json_files[0].read_text())
    assert payload["requests"] == 3
    with csv_files[0].open() as handle:
        assert len(list(csv.DictReader(handle))) == 3
