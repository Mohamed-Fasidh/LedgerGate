import httpx

from app.services.inference.prometheus import scrape_prometheus


def test_scrapes_vllm_cache_and_dcgm_gpu_metrics(monkeypatch):
    class Response:
        text = """# TYPE DCGM_FI_DEV_GPU_UTIL gauge
DCGM_FI_DEV_GPU_UTIL{gpu="0"} 73 123456
DCGM_FI_DEV_FB_USED{gpu="0"} 8192
vllm:gpu_cache_usage_perc{engine="0"} 0.45
vllm:num_requests_running 3
vllm:num_requests_waiting 2
"""
        def raise_for_status(self):
            pass
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: Response())
    metrics = scrape_prometheus("http://metrics.test/metrics")
    assert metrics["gpu_utilization_percent"] == 73
    assert metrics["gpu_memory_used_bytes"] == 8192 * 1024 * 1024
    assert metrics["vllm_kv_cache_usage_percent"] == 45
    assert metrics["vllm_requests_running"] == 3
    assert metrics["vllm_requests_waiting"] == 2
