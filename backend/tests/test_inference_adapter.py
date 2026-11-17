import pytest

from app.services import model_router
from app.services.inference import InferenceResult


class FakeClient:
    def generate(self, messages, max_tokens=None):
        assert "approval decision" in messages[0]["content"]
        return InferenceResult(
            "advisory only", "vllm", "fake", 4, 2, 6,
            end_to_end_latency_ms=12.5,
        )

    def stream(self, messages, max_tokens=None):
        yield "advisory "
        yield "stream"


def test_existing_model_router_uses_shared_inference_client(monkeypatch):
    monkeypatch.setattr(model_router, "_client", lambda: FakeClient())
    result = model_router.explain_findings({"id": 7}, [{"kind": "variance"}], [])
    assert result["provider"] == "vllm"
    assert result["explanation"] == "advisory only"
    assert result["usage"]["total_tokens"] == 6
    assert result["latency_ms"] == 12.5


def test_streaming_adapter_yields_incremental_text(monkeypatch):
    monkeypatch.setattr(model_router, "_client", lambda: FakeClient())
    assert "".join(model_router.stream_explanation({}, [], [])) == "advisory stream"


def test_none_provider_remains_disabled(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.delenv("INFERENCE_PROVIDER", raising=False)
    with pytest.raises(model_router.ModelUnavailable, match="LLM_PROVIDER"):
        model_router.explain_findings({}, [], [])
