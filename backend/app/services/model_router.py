"""Advisory model adapter backed by the shared Ollama/vLLM inference client.

The model explains deterministic findings only. It never approves invoices,
changes financial records, or initiates payment.
"""
import os

from app.services.inference import InferenceClient, InferenceConfig, InferenceError


class ModelUnavailable(RuntimeError):
    pass


def _client() -> InferenceClient:
    provider = (os.getenv("INFERENCE_PROVIDER") or os.getenv("LLM_PROVIDER", "none")).lower()
    if provider not in {"ollama", "vllm"}:
        raise ModelUnavailable("Set LLM_PROVIDER to 'ollama' or 'vllm' to enable advisory explanations")
    try:
        return InferenceClient(InferenceConfig.from_env())
    except Exception as exc:
        raise ModelUnavailable(f"Invalid inference configuration: {type(exc).__name__}") from exc


def explain_findings(invoice: dict, findings: list[dict], evidence: list[dict]) -> dict:
    prompt = (
        "You are an assistant for a finance reviewer. Explain the supplied deterministic "
        "reconciliation findings in plain language. Use only supplied evidence. Do not "
        "invent facts, make an approval decision, or authorize payment. Say when evidence "
        "is insufficient.\n"
        f"Invoice: {invoice}\nFindings: {findings}\nEvidence: {evidence}"
    )
    try:
        result = _client().generate([{"role": "user", "content": prompt}], max_tokens=500)
        return {
            "provider": result.provider, "model": result.model, "explanation": result.text,
            "usage": {
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": result.total_tokens,
            },
            "latency_ms": result.end_to_end_latency_ms,
        }
    except InferenceError as exc:
        raise ModelUnavailable(f"Configured model provider failed: {type(exc).__name__}") from exc


def stream_explanation(invoice: dict, findings: list[dict], evidence: list[dict]):
    """Yield advisory text deltas; closing the iterator cancels the provider request."""
    prompt = (
        "Explain the deterministic financial findings using only the supplied evidence. "
        "Never approve, reject, modify records, or authorize payment. State uncertainty.\n"
        f"Invoice: {invoice}\nFindings: {findings}\nEvidence: {evidence}"
    )
    try:
        yield from _client().stream([{"role": "user", "content": prompt}], max_tokens=500)
    except InferenceError as exc:
        raise ModelUnavailable(f"Configured model provider failed: {type(exc).__name__}") from exc
