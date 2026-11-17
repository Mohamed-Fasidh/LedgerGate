"""Small Prometheus text-format reader for optional vLLM/DCGM exporters."""
from __future__ import annotations

import httpx


def scrape_prometheus(url: str, timeout: float = 2.0) -> dict:
    if not url.startswith(("http://", "https://")):
        raise ValueError("Prometheus URL must use HTTP or HTTPS")
    response = httpx.get(url, timeout=timeout, follow_redirects=False)
    response.raise_for_status()
    values = {}
    for line in response.text.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        metric, raw = parts[0], parts[1]
        try:
            values[metric] = float(raw)
        except ValueError:
            continue
    selected = {}
    for key, value in values.items():
        if key.startswith("DCGM_FI_DEV_GPU_UTIL"):
            selected["gpu_utilization_percent"] = value
        elif key.startswith("DCGM_FI_DEV_FB_USED"):
            selected["gpu_memory_used_bytes"] = int(value * 1024 * 1024)
        elif key.startswith("vllm:gpu_cache_usage_perc"):
            selected["vllm_kv_cache_usage_percent"] = value * 100 if value <= 1 else value
        elif key.startswith("vllm:num_requests_running"):
            selected["vllm_requests_running"] = value
        elif key.startswith("vllm:num_requests_waiting"):
            selected["vllm_requests_waiting"] = value
    return selected
