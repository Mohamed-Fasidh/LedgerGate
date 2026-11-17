# LedgerGate inference engineering and benchmarking

## Design boundary

LedgerGate is an application-side inference client and benchmark controller. It does **not** implement a transformer runtime, GPU kernel, continuous-batching scheduler, paged attention, or KV-cache allocator.

- **vLLM** owns the serving scheduler, continuous batching, paged KV cache, tensor parallelism, and supported quantization kernels. LedgerGate controls application admission/backpressure and sends concurrent requests.
- **Ollama** owns model loading and model-artifact quantization. The client sends supported generation/context/batch options and keep-alive configuration.
- The existing `app.services.model_router.explain_findings` adapter calls the shared client. Model output remains advisory: deterministic reconciliation and human approval remain authoritative.

## Installation

The core API requirements do not install GPU runtimes. On a benchmark client:

```bash
cd backend
pip install -r requirements.txt
pip install -r requirements-inference.txt  # optional telemetry, charts, local tokenizer
```

Install PyTorch and vLLM using the official compatibility guidance for the actual CUDA/ROCm/CPU host. Install Ollama separately when needed. Do not add CUDA or vLLM dependencies to the production API image merely to expose the remote-client interface.

## Configuration

Existing variables remain supported: `LLM_PROVIDER`, `OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `VLLM_BASE_URL`, `VLLM_MODEL`, and `VLLM_API_KEY`.

| Variable | Default | Meaning |
|---|---:|---|
| `INFERENCE_PROVIDER` | `LLM_PROVIDER` | Select `ollama` or `vllm` |
| `INFERENCE_TIMEOUT_SECONDS` | 60 | Request deadline |
| `INFERENCE_CONNECT_TIMEOUT_SECONDS` | 5 | Connection timeout |
| `INFERENCE_MAX_CONCURRENT` | 8 | In-flight application request slots |
| `INFERENCE_MAX_QUEUE_SIZE` | 32 | Number of waiting callers; excess is rejected |
| `INFERENCE_QUEUE_WAIT_SECONDS` | 5 | Maximum wait for an admission slot |
| `INFERENCE_MAX_RETRIES` | 1 | Retry transient failures only; 4xx are not retried except 429 |
| `INFERENCE_RETRY_BACKOFF_SECONDS` | 0.25 | Exponential retry base |
| `INFERENCE_MAX_OUTPUT_TOKENS` | 1024 | Hard output-token cap |
| `INFERENCE_TEMPERATURE` | 0.1 | Sampling temperature |
| `OLLAMA_NUM_CTX` | unset | Requested context size |
| `OLLAMA_NUM_BATCH` | unset | Prompt batch size |
| `OLLAMA_NUM_GPU` | unset | Ollama GPU-layer hint; verify actual server behavior |
| `OLLAMA_KEEP_ALIVE` | unset | Model keep-alive |
| `INFERENCE_PROMETHEUS_URL` | unset | Optional vLLM/DCGM Prometheus endpoint |
| `BENCHMARK_TOKENIZER` | unset | Path to a locally cached Transformers tokenizer |
| `INFERENCE_MODEL_REVISION` | unset | Revision label written into benchmark metadata |

Streaming requests are not retried after bytes begin streaming, because replaying a partial response could duplicate user-visible output. Closing the generator closes the HTTP response and releases the concurrency slot. The request output cap cannot be overridden by Ollama request options.

## vLLM launch profiles

`experiments.json` contains baseline and candidate configurations. These are **proposed experiments, not measured results**. Generate a launch command without executing it:

```bash
python scripts/vllm_profile.py baseline_fp16 --model Qwen/Qwen2.5-3B-Instruct
python scripts/vllm_profile.py optimized_bf16 --model Qwen/Qwen2.5-3B-Instruct
python scripts/vllm_profile.py kv_cache_fp8 --model Qwen/Qwen2.5-3B-Instruct
python scripts/vllm_profile.py awq_4bit --model your-org/your-awq-checkpoint
python scripts/vllm_profile.py bitsandbytes_int8 --model your-org/your-int8-checkpoint
```

Check the generated command against the installed `vllm serve --help`. Support varies by vLLM version, accelerator, model architecture, and checkpoint metadata. BF16 requires compatible hardware. FP8 KV cache requires compatible kernels. AWQ and BitsAndBytes require a checkpoint prepared for the selected format; selecting a flag does not quantize an arbitrary model.

The `--max-num-seqs` and `--max-num-batched-tokens` controls affect scheduling and memory pressure. `--gpu-memory-utilization`, `--max-model-len`, and `--kv-cache-dtype` affect available cache capacity and representation. Change one variable at a time before combining optimizations.

## Ollama

Ollama quantization is selected by the model artifact/tag, not by a generic client-side quantization flag. The client can pass `num_ctx`, `num_batch`, `num_predict`, `num_gpu`, and `keep_alive` when configured. The benchmark does not infer GPU execution from `num_gpu`; inspect the inference host's device/runtime telemetry.

Server-level concurrency/queue variables such as `OLLAMA_NUM_PARALLEL` and `OLLAMA_MAX_QUEUE` must be configured on the Ollama host. Confirm exact names and supported values against the installed Ollama version.

## Benchmark runs

Run from the repository root once the selected model server is healthy:

```bash
PYTHONPATH=backend python -m app.services.inference.benchmark --provider vllm --base-url http://localhost:8001/v1 --model Qwen/Qwen2.5-3B-Instruct --label baseline_fp16 --concurrency 1 --requests 30
PYTHONPATH=backend python -m app.services.inference.benchmark --provider vllm --base-url http://localhost:8001/v1 --model Qwen/Qwen2.5-3B-Instruct --label optimized_bf16 --concurrency 8 --requests 100
PYTHONPATH=backend python -m app.services.inference.benchmark --provider ollama --base-url http://localhost:11434 --model qwen2.5:3b --label ollama_q4 --concurrency 4 --requests 40
```

Each run writes JSON summary and CSV request rows. Recorded measurements include provider/model, revision/environment metadata, request outcomes, TTFT, estimated inter-token latency, token/request throughput, p50/p95 latency, health status, process memory, and GPU/server metrics where available.

Token counting uses provider-reported completion tokens when available; otherwise it labels counts from an explicitly configured local tokenizer or a whitespace estimate. Streaming chunks do not necessarily align to model tokens, so inter-token latency is an estimate from chunk arrival timing. Exact token-level ITL requires serving-engine instrumentation. GPU fields remain null unless local NVML or remote DCGM metrics are available.

Generate comparison charts only from real benchmark output files:

```bash
PYTHONPATH=backend python -c "from app.services.inference.benchmark import plot_comparison; plot_comparison(['benchmark-results/ACTUAL_BASELINE.json', 'benchmark-results/ACTUAL_OPTIMIZED.json'], 'benchmark-results/graphs')"
```

## Baseline-versus-optimized protocol

1. Record GPU, VRAM, driver, CUDA/ROCm, framework and server versions, model revision, dtype, quantization, KV-cache dtype, context size, and all launch flags.
2. Warm up for at least five requests and exclude warm-up samples from reported measurements.
3. Keep prompt corpus, output cap, model revision and decoding parameters fixed.
4. Sweep concurrency (for example 1, 2, 4, 8, 16), changing one setting at a time.
5. Repeat each profile at least three times and preserve JSON/CSV outputs.
6. Compare TTFT, estimated ITL, tokens/s, requests/s, p50/p95 latency, GPU memory/utilization and vLLM KV-cache usage.
7. Reject configurations with OOMs, unacceptable tail latency, output-quality regressions, or unsupported dtype/quantization.

No benchmark numbers are committed. The benchmark output directory is produced only when commands run against an actually reachable provider.

## Safety and operational limits

- Never expose provider credentials to browsers or tenants.
- Authenticate and authorize tenant context before constructing prompts; do not mix tenant data without authorization.
- The inference client exposes no tool execution, SQL, approval, settlement, or payment action.
- Protect model-serving endpoints with network policy, authentication and TLS.
- Monitor queue rejections, provider errors, tail latency and server-side GPU metrics.
- Deterministic reconciliation, authorization checks, audit events and human approval stay authoritative.
