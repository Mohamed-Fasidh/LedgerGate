# LLM Inference and GPU Performance

**Duration:** 18 minutes

## Learning objectives
Explain prefill, decode, TTFT, inter-token latency, throughput, batching, queueing, backpressure, KV-cache memory, quantization, and reproducible benchmarking.

## Prerequisites
HTTP APIs, concurrency, and basic GPU memory concepts.

## Whiteboard concepts
- Prefill processes the prompt and constructs attention key/value state.
- Decode generates tokens autoregressively while reusing cached keys and values.
- TTFT is request-start to first generated token.
- Inter-token latency is time between generated tokens; chunk-based timing is an estimate unless token timestamps are available.
- Throughput must specify concurrency and measurement window.
- KV-cache memory approximately scales as 2 × layers × active sequences × cached tokens × KV heads × head dimension × bytes per element.
- Batching can improve utilization but increase queueing delay. Continuous batching dynamically admits and retires requests.
- Quantization reduces weight memory, but speed and quality depend on hardware, kernels, and workload.

## Timed teaching script
- 0–3 min: prefill versus decode.
- 3–6 min: define TTFT, inter-token latency, end-to-end latency, and throughput.
- 6–9 min: derive KV-cache scaling and discuss quantization.
- 9–12 min: queueing, bounded concurrency, batching, and backpressure.
- 12–16 min: inspect backend/app/services/inference/benchmark.py and docs/INFERENCE_ENGINEERING.md.
- 16–18 min: design a controlled concurrency experiment.

## Coding demonstration
Install optional dependencies from backend/requirements-inference.txt. Confirm an Ollama or OpenAI-compatible vLLM endpoint and record the model identifier. Run a small benchmark at concurrency 1, then at a documented higher concurrency. Save JSON/CSV outputs and compare TTFT, inter-token latency, end-to-end latency, and throughput. Record hardware/software snapshots and tokenizer source. Report unavailable GPU telemetry as unavailable, not zero.

## Common mistakes
- Treating whitespace word counts as exact token counts.
- Comparing warm and cold runs without labels.
- Reporting averages without tail percentiles.
- Confusing client GPU utilization with server GPU utilization.
- Calling concurrent requests continuous batching.
- Assuming quantization always improves speed.
- Publishing results without model, hardware, runtime, and configuration metadata.

## Applications
Chat assistants, code generation, RAG, and high-concurrency enterprise inference.

## Interview questions
1. Why can TTFT rise while aggregate throughput improves?
2. What determines KV-cache memory?
3. How does continuous batching differ from static batching?
4. What is backpressure?
5. Which metadata is essential for fair benchmark comparison?

## Student exercise
Design a benchmark matrix with two concurrency levels and two prompt lengths. Specify controls, warm-up, repetitions, and metrics. Explain how unavailable GPU telemetry should be reported.

## Instructor note
This lesson contains no performance claims. All measurements must come from the actual model server and hardware tested.
