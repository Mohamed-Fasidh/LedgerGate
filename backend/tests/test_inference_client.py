import json

import httpx
import pytest

from app.services.inference import InferenceClient, InferenceConfig, InferenceError
from app.services.inference.metrics import hardware_snapshot


def config(provider="vllm", **kwargs):
    defaults = dict(
        provider=provider,
        base_url="http://model.test/v1" if provider == "vllm" else "http://ollama.test",
        model="test-model", api_key="test-key", max_concurrent_requests=2,
        max_queue_wait_seconds=0.05, max_retries=0,
    )
    defaults.update(kwargs)
    return InferenceConfig(**defaults)


def test_vllm_non_streaming_response_and_usage():
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-key"
        payload = json.loads(request.content)
        assert payload["max_tokens"] <= 1024
        return httpx.Response(200, json={
            "id": "r1", "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        })
    result = InferenceClient(config(), transport=httpx.MockTransport(handler)).generate(
        [{"role": "user", "content": "hello"}]
    )
    assert result.text == "ok" and result.total_tokens == 5 and result.provider == "vllm"


def test_ollama_non_streaming_response():
    def handler(request):
        assert request.url.path == "/api/generate"
        body = json.loads(request.content)
        assert body["model"] == "test-model"
        return httpx.Response(200, json={
            "response": "hello", "prompt_eval_count": 4, "eval_count": 6,
        })
    result = InferenceClient(config("ollama"), transport=httpx.MockTransport(handler)).generate(
        [{"role": "user", "content": "hello"}]
    )
    assert result.text == "hello" and result.total_tokens == 10


def test_vllm_streaming_deltas():
    body = (b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
            b'data: [DONE]\n\n')
    client = InferenceClient(config(), transport=httpx.MockTransport(
        lambda req: httpx.Response(200, content=body)))
    assert "".join(client.stream([{"role": "user", "content": "hi"}])) == "hello"


def test_ollama_streaming_deltas():
    body = b'{"response":"a","done":false}\n{"response":"b","done":true}\n'
    client = InferenceClient(config("ollama"), transport=httpx.MockTransport(
        lambda req: httpx.Response(200, content=body)))
    assert "".join(client.stream([{"role": "user", "content": "hi"}])) == "ab"


def test_http_4xx_is_not_retried():
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"error": "bad request"})
    client = InferenceClient(config(max_retries=3), transport=httpx.MockTransport(handler))
    with pytest.raises(InferenceError):
        client.generate([{"role": "user", "content": "hi"}])
    assert len(calls) == 1


def test_queue_backpressure_rejects_when_slot_not_available():
    client = InferenceClient(config(
        max_concurrent_requests=1, max_queue_size=0, max_queue_wait_seconds=0.01,
    ), transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})))
    assert client._slots.acquire(blocking=False)
    try:
        with pytest.raises(InferenceError, match="queue is full"):
            client.generate([{"role": "user", "content": "hi"}])
    finally:
        client._slots.release()


def test_stream_iterator_close_releases_admission_slot():
    body = b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
    client = InferenceClient(config(max_concurrent_requests=1),
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=body)))
    iterator = client.stream([{"role": "user", "content": "hi"}])
    next(iterator)
    iterator.close()
    assert client._slots.acquire(blocking=False)
    client._slots.release()


def test_cpu_hardware_snapshot_is_truthful():
    result = hardware_snapshot()
    assert "cuda_available" in result
    assert "gpu_utilization_percent" in result
    assert result["cuda_available"] in (True, False)


def test_invalid_config_rejected():
    with pytest.raises(ValueError):
        config(max_concurrent_requests=0)


def test_ollama_options_cannot_override_max_output_tokens():
    captured = {}
    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"response": "ok"})
    client = InferenceClient(config("ollama", max_output_tokens=32),
        transport=httpx.MockTransport(handler))
    client.generate([{"role": "user", "content": "hello"}], options={"num_predict": 99999})
    assert captured["options"]["num_predict"] == 32


def test_vllm_health_reports_model_presence():
    def handler(request):
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "test-model"}]})
    health = InferenceClient(config(), transport=httpx.MockTransport(handler)).health()
    assert health["healthy"] is True and health["model_available"] is True


def test_model_health_missing_is_distinct_from_server_health():
    client = InferenceClient(config(), transport=httpx.MockTransport(
        lambda req: httpx.Response(200, json={"data": [{"id": "another-model"}]})))
    health = client.health()
    assert health["healthy"] is True and health["model_available"] is False


def test_invalid_temperature_rejected():
    with pytest.raises(ValueError, match="temperature"):
        config(temperature=2.5)


def test_config_from_env_uses_inference_provider_override(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.setenv("INFERENCE_PROVIDER", "vllm")
    monkeypatch.setenv("VLLM_BASE_URL", "http://vllm.test/v1")
    cfg = InferenceConfig.from_env()
    assert cfg.provider == "vllm" and cfg.base_url == "http://vllm.test/v1"


def test_config_rejects_non_finite_timeout():
    with pytest.raises(ValueError, match="finite"):
        config(request_timeout_seconds=float("inf"))


def test_transient_server_error_is_retried():
    calls = []
    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": "temporarily unavailable"})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "ok"}}], "usage": {},
        })
    client = InferenceClient(config(max_retries=1, retry_backoff_seconds=0),
        transport=httpx.MockTransport(handler))
    assert client.generate([{"role": "user", "content": "hello"}]).text == "ok"
    assert len(calls) == 2


def test_transport_timeout_is_reported_without_fake_success():
    def handler(request):
        raise httpx.ReadTimeout("slow model server")
    client = InferenceClient(config(), transport=httpx.MockTransport(handler))
    with pytest.raises(InferenceError, match="inference failed"):
        client.generate([{"role": "user", "content": "hello"}])


def test_vllm_stream_usage_event_is_exposed():
    body = (b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
            b'data: {"choices":[],"usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}\n\n'
            b'data: [DONE]\n\n')
    client = InferenceClient(config(), transport=httpx.MockTransport(
        lambda req: httpx.Response(200, content=body)))
    events = list(client.stream_events([{"role": "user", "content": "hi"}]))
    assert events[0] == {"type": "text", "text": "hello"}
    assert events[1]["completion_tokens"] == 2
    assert events[1]["source"] == "vllm_stream_usage"


def test_ollama_stream_usage_event_is_exposed():
    body = (b'{"response":"hi","done":false}\n'
            b'{"response":"","done":true,"prompt_eval_count":3,"eval_count":2}\n')
    client = InferenceClient(config("ollama"), transport=httpx.MockTransport(
        lambda req: httpx.Response(200, content=body)))
    events = list(client.stream_events([{"role": "user", "content": "hi"}]))
    usage = [event for event in events if event["type"] == "usage"][0]
    assert usage["completion_tokens"] == 2 and usage["source"] == "ollama_stream_usage"


def test_ollama_batch_context_and_keep_alive_configuration(monkeypatch):
    monkeypatch.setenv("OLLAMA_NUM_CTX", "8192")
    monkeypatch.setenv("OLLAMA_NUM_BATCH", "256")
    monkeypatch.setenv("OLLAMA_NUM_GPU", "0")
    monkeypatch.setenv("OLLAMA_KEEP_ALIVE", "10m")
    cfg = InferenceConfig.from_env(provider_override="ollama")
    captured = {}
    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"response": "ok"})
    InferenceClient(cfg, transport=httpx.MockTransport(handler)).generate(
        [{"role": "user", "content": "hello"}], options={"num_predict": 999})
    assert captured["options"]["num_ctx"] == 8192
    assert captured["options"]["num_batch"] == 256
    assert captured["options"]["num_gpu"] == 0
    assert captured["options"]["num_predict"] == cfg.max_output_tokens
    assert captured["keep_alive"] == "10m"
