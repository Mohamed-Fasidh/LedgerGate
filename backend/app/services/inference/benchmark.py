from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import statistics
import threading
import time
import uuid
from functools import lru_cache
from pathlib import Path

from .client import InferenceClient, InferenceConfig
from .metrics import hardware_snapshot, software_snapshot
from .prometheus import scrape_prometheus


@lru_cache(maxsize=1)
def _load_tokenizer(model: str):
    from transformers import AutoTokenizer
    return AutoTokenizer.from_pretrained(model, local_files_only=True)


def _token_count(text: str) -> tuple[int, str]:
    """Use a locally cached tokenizer when explicitly configured; otherwise estimate."""
    try:
        model = os.getenv("BENCHMARK_TOKENIZER", "")
        if model:
            return len(_load_tokenizer(model).encode(text)), "transformers_local_tokenizer"
    except Exception:
        pass
    return len(text.split()), "whitespace_estimate"


def _resource_snapshot() -> dict:
    result = hardware_snapshot()
    try:
        import psutil
        result["process_rss_bytes"] = psutil.Process().memory_info().rss
    except ImportError:
        result["process_rss_bytes"] = None
    return result


class ResourceSampler:
    def __init__(self, interval: float = 0.2, metrics_url: str | None = None):
        if interval <= 0:
            raise ValueError("sampling interval must be positive")
        self.interval = interval
        self.metrics_url = metrics_url
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.samples: list[dict] = []

    def start(self):
        if self.thread and self.thread.is_alive():
            raise RuntimeError("resource sampler is already running")
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _sample(self):
        sample = _resource_snapshot()
        if self.metrics_url:
            try:
                sample["server_metrics"] = scrape_prometheus(self.metrics_url)
            except Exception as exc:
                sample["server_metrics_error"] = type(exc).__name__
        self.samples.append(sample)

    def _run(self):
        while not self.stop_event.is_set():
            self._sample()
            self.stop_event.wait(self.interval)

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)

    def summary(self) -> dict:
        def values(path: tuple[str, ...]):
            result = []
            for sample in self.samples:
                item = sample
                for key in path:
                    item = item.get(key) if isinstance(item, dict) else None
                if item is not None:
                    result.append(item)
            return result
        rss = values(("process_rss_bytes",))
        local_gpu_mem = values(("gpu_memory_used_bytes",))
        server_gpu_mem = values(("server_metrics", "gpu_memory_used_bytes"))
        local_gpu_util = values(("gpu_utilization_percent",))
        server_gpu_util = values(("server_metrics", "gpu_utilization_percent"))
        kv_cache = values(("server_metrics", "vllm_kv_cache_usage_percent"))
        running = values(("server_metrics", "vllm_requests_running"))
        waiting = values(("server_metrics", "vllm_requests_waiting"))
        return {
            "peak_process_rss_bytes": max(rss) if rss else None,
            "peak_gpu_memory_used_bytes": max(server_gpu_mem or local_gpu_mem)
                if (server_gpu_mem or local_gpu_mem) else None,
            "mean_gpu_utilization_percent": statistics.mean(server_gpu_util or local_gpu_util)
                if (server_gpu_util or local_gpu_util) else None,
            "gpu_utilization_source": "remote_prometheus_dcgm" if server_gpu_util
                else ("local_nvml" if local_gpu_util else None),
            "mean_vllm_kv_cache_usage_percent": statistics.mean(kv_cache) if kv_cache else None,
            "max_vllm_requests_running": max(running) if running else None,
            "max_vllm_requests_waiting": max(waiting) if waiting else None,
            "gpu_samples": len(server_gpu_util or local_gpu_util),
        }


class BenchmarkSampler(ResourceSampler):
    pass


def _one_request(client: InferenceClient, messages: list[dict], run_id: str) -> dict:
    started = time.perf_counter()
    first = last = last_chunk_time = None
    token_gaps: list[float] = []
    chunks: list[str] = []
    provider_usage = None
    try:
        event_stream = (client.stream_events(messages) if hasattr(client, "stream_events")
                        else client.stream(messages))
        for event in event_stream:
            if isinstance(event, dict):
                if event.get("type") == "usage":
                    provider_usage = event
                    continue
                if event.get("type") != "text":
                    continue
                chunk = event.get("text", "")
            else:
                chunk = event
            if not chunk:
                continue
            now = time.perf_counter()
            if first is None:
                first = now
            if last_chunk_time is not None:
                delta_tokens, _ = _token_count(chunk)
                token_gaps.extend([(now - last_chunk_time) * 1000 /
                                   max(1, delta_tokens)] * max(1, delta_tokens))
            last_chunk_time = now
            last = now
            chunks.append(chunk)

        elapsed_ms = (time.perf_counter() - started) * 1000
        output = "".join(chunks)
        tokens, token_source = _token_count(output)
        if provider_usage and provider_usage.get("completion_tokens") is not None:
            tokens = int(provider_usage["completion_tokens"])
            token_source = provider_usage.get("source", "provider_stream_usage")
        generation_seconds = max(last - first, 1e-9) if first is not None and last is not None else None
        return {
            "request_id": run_id, "status": "success",
            "ttft_ms": (first - started) * 1000 if first is not None else None,
            "inter_token_latency_ms_estimate": statistics.mean(token_gaps) if token_gaps else None,
            "inter_token_latency_ms_p50_estimate": statistics.median(token_gaps) if token_gaps else None,
            "output_tokens": tokens, "token_count_source": token_source,
            "tokens_per_second": tokens / generation_seconds if generation_seconds else None,
            "end_to_end_latency_ms": elapsed_ms, "error": None,
        }
    except Exception as exc:
        return {
            "request_id": run_id, "status": "error", "ttft_ms": None,
            "inter_token_latency_ms_estimate": None, "inter_token_latency_ms_p50_estimate": None,
            "output_tokens": 0, "token_count_source": None, "tokens_per_second": None,
            "end_to_end_latency_ms": (time.perf_counter() - started) * 1000,
            "error": type(exc).__name__,
        }


async def run_benchmark(*, config: InferenceConfig, concurrency: int = 1, requests: int = 10,
                        prompt: str = "Explain invoice reconciliation in three concise points.",
                        output_dir: str = "benchmark-results", label: str = "baseline",
                        metrics_url: str | None = None) -> dict:
    if concurrency < 1 or requests < 1:
        raise ValueError("concurrency and requests must be positive")
    client = InferenceClient(config)
    health = client.health()
    if not health.get("healthy") or health.get("model_available") is False:
        raise RuntimeError(f"Inference provider/model is not healthy: {health}")
    messages = [{"role": "user", "content": prompt}]
    sampler = BenchmarkSampler(metrics_url=metrics_url)
    sampler.start()
    started = time.perf_counter()
    loop = asyncio.get_running_loop()
    semaphore = asyncio.Semaphore(concurrency)

    async def task(index: int):
        async with semaphore:
            run_id = f"{label}-{index}-{uuid.uuid4().hex[:8]}"
            return await loop.run_in_executor(None, _one_request, client, messages, run_id)

    try:
        results = await asyncio.gather(*(task(index) for index in range(requests)))
    finally:
        total_seconds = time.perf_counter() - started
        sampler.stop()

    successful = [row for row in results if row["status"] == "success"]
    latencies = [row["end_to_end_latency_ms"] for row in successful]
    ttfts = [row["ttft_ms"] for row in successful if row["ttft_ms"] is not None]
    itls = [row["inter_token_latency_ms_estimate"] for row in successful
            if row["inter_token_latency_ms_estimate"] is not None]
    total_tokens = sum(row["output_tokens"] for row in successful)
    summary = {
        "label": label, "provider": config.provider, "model": config.model,
        "base_url": config.base_url, "concurrency": concurrency, "requests": requests,
        "successful_requests": len(successful), "failed_requests": requests - len(successful),
        "wall_time_seconds": total_seconds,
        "requests_per_second": len(successful) / total_seconds if total_seconds else 0,
        "aggregate_output_tokens": total_tokens,
        "aggregate_tokens_per_second": total_tokens / total_seconds if total_seconds else 0,
        "ttft_ms_mean": statistics.mean(ttfts) if ttfts else None,
        "ttft_ms_p50": statistics.median(ttfts) if ttfts else None,
        "inter_token_latency_ms_estimate_mean": statistics.mean(itls) if itls else None,
        "end_to_end_latency_ms_mean": statistics.mean(latencies) if latencies else None,
        "end_to_end_latency_ms_p50": statistics.median(latencies) if latencies else None,
        "end_to_end_latency_ms_p95": sorted(latencies)[
            min(len(latencies) - 1, max(0, math.ceil(0.95 * len(latencies)) - 1))
        ] if latencies else None,
        "resource_summary": sampler.summary(), "hardware": hardware_snapshot(),
        "software": software_snapshot(), "server_health": health,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "server_version": health.get("server_version"),
        "model_revision": os.getenv("INFERENCE_MODEL_REVISION"),
        "experiment_config": {key: os.getenv(key) for key in (
            "INFERENCE_EXPERIMENT_DTYPE", "INFERENCE_EXPERIMENT_QUANTIZATION",
            "INFERENCE_EXPERIMENT_KV_CACHE_DTYPE", "VLLM_GPU_MEMORY_UTILIZATION",
            "VLLM_MAX_NUM_SEQS", "VLLM_MAX_NUM_BATCHED_TOKENS", "VLLM_MAX_MODEL_LEN",
            "OLLAMA_NUM_PARALLEL", "OLLAMA_MAX_QUEUE", "OLLAMA_NUM_CTX",
            "OLLAMA_NUM_BATCH", "OLLAMA_NUM_GPU", "OLLAMA_KEEP_ALIVE",
        )},
        "measurement_notes": {
            "token_counts": "local tokenizer if configured and cached; otherwise whitespace estimate",
            "inter_token_latency": "estimated from streaming chunk intervals and local token estimates; chunks are not guaranteed to align to model tokens",
            "gpu_utilization": "local NVML or optional remote DCGM Prometheus exporter; null means unavailable",
            "continuous_batching": "owned by the vLLM server; inspect server metrics separately",
        },
        "requests_detail": results,
    }
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    stem = f"{label}-{config.provider}-c{concurrency}-{int(time.time())}"
    (output / f"{stem}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    with (output / f"{stem}.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "request_id", "status", "ttft_ms", "inter_token_latency_ms_estimate",
            "inter_token_latency_ms_p50_estimate", "output_tokens", "token_count_source",
            "tokens_per_second", "end_to_end_latency_ms", "error",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)
    return summary


def plot_comparison(json_files: list[str], output: str):
    """Create comparison charts only from supplied, existing benchmark JSON files."""
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError("Install matplotlib to generate benchmark graphs") from exc
    rows = [json.loads(Path(path).read_text(encoding="utf-8")) for path in json_files]
    labels = [f"{row['label']}\nC={row['concurrency']}" for row in rows]
    specifications = [
        ("ttft_ms_mean", "Time to first token (ms)", "ttft.png"),
        ("inter_token_latency_ms_estimate_mean", "Estimated inter-token latency (ms)", "inter_token_latency.png"),
        ("end_to_end_latency_ms_mean", "Mean end-to-end latency (ms)", "latency.png"),
        ("requests_per_second", "Requests per second", "throughput.png"),
        ("aggregate_tokens_per_second", "Aggregate output tokens per second", "tokens_per_second.png"),
        (("resource_summary", "peak_process_rss_bytes"), "Peak process RSS (bytes)", "peak_process_memory.png"),
        (("resource_summary", "peak_gpu_memory_used_bytes"), "Peak GPU memory (bytes)", "peak_gpu_memory.png"),
        (("resource_summary", "mean_gpu_utilization_percent"), "Mean GPU utilization (%)", "gpu_utilization.png"),
        (("resource_summary", "mean_vllm_kv_cache_usage_percent"), "Mean vLLM KV cache usage (%)", "kv_cache_usage.png"),
    ]
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    for key, title, filename in specifications:
        values = [row.get(key) if isinstance(key, str) else row.get(key[0], {}).get(key[1])
                  for row in rows]
        if not any(value is not None for value in values):
            continue
        plt.figure(figsize=(max(6, len(labels) * 1.2), 4))
        plt.bar(labels, [float("nan") if value is None else value for value in values])
        plt.title(title)
        plt.ylabel(title)
        plt.tight_layout()
        plt.savefig(out / filename, dpi=140)
        plt.close()


def main():
    parser = argparse.ArgumentParser(description="Reproducible LedgerGate model-serving benchmark")
    provider = os.getenv("INFERENCE_PROVIDER", os.getenv("LLM_PROVIDER", "ollama")).lower()
    if provider not in {"ollama", "vllm"}:
        provider = "ollama"
    parser.add_argument("--provider", choices=["ollama", "vllm"], default=provider)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--requests", type=int, default=10)
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--output-dir", default="benchmark-results")
    parser.add_argument("--prompt", default="Explain invoice reconciliation in three concise points.")
    parser.add_argument("--metrics-url", default=os.getenv("INFERENCE_PROMETHEUS_URL"))
    args = parser.parse_args()
    defaults = InferenceConfig.from_env(provider_override=args.provider)
    config = InferenceConfig(
        provider=args.provider, base_url=args.base_url or defaults.base_url,
        model=args.model or defaults.model, api_key=defaults.api_key,
        request_timeout_seconds=defaults.request_timeout_seconds,
        connect_timeout_seconds=defaults.connect_timeout_seconds,
        max_concurrent_requests=max(args.concurrency, defaults.max_concurrent_requests),
        max_queue_size=defaults.max_queue_size, max_queue_wait_seconds=defaults.max_queue_wait_seconds,
        max_retries=defaults.max_retries, retry_backoff_seconds=defaults.retry_backoff_seconds,
        max_output_tokens=defaults.max_output_tokens, temperature=defaults.temperature,
        ollama_options=defaults.ollama_options, ollama_keep_alive=defaults.ollama_keep_alive,
    )
    result = asyncio.run(run_benchmark(
        config=config, concurrency=args.concurrency, requests=args.requests,
        prompt=args.prompt, output_dir=args.output_dir, label=args.label,
        metrics_url=args.metrics_url,
    ))
    print(json.dumps({key: value for key, value in result.items() if key != "requests_detail"}, indent=2))


if __name__ == "__main__":
    main()
