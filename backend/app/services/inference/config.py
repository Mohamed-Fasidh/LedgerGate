from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Literal

Provider = Literal["ollama", "vllm"]


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _optional_int(name: str, minimum: int = 0, maximum: int = 131072) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class InferenceConfig:
    provider: Provider = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = "qwen2.5:3b"
    api_key: str = ""
    request_timeout_seconds: float = 60.0
    connect_timeout_seconds: float = 5.0
    max_concurrent_requests: int = 8
    max_queue_size: int = 32
    max_queue_wait_seconds: float = 5.0
    max_retries: int = 1
    retry_backoff_seconds: float = 0.25
    max_output_tokens: int = 1024
    temperature: float = 0.1
    ollama_options: dict = field(default_factory=dict)
    ollama_keep_alive: str | None = None

    @classmethod
    def from_env(cls, provider_override: str | None = None) -> "InferenceConfig":
        provider = (provider_override or os.getenv("INFERENCE_PROVIDER") or
                    os.getenv("LLM_PROVIDER", "ollama")).lower()
        if provider not in {"ollama", "vllm"}:
            raise ValueError("INFERENCE_PROVIDER must be ollama or vllm")
        if provider == "vllm":
            base = os.getenv("VLLM_BASE_URL", "http://localhost:8001/v1").rstrip("/")
            model = os.getenv("VLLM_MODEL", "Qwen/Qwen2.5-3B-Instruct")
            key = os.getenv("VLLM_API_KEY", "EMPTY")
        else:
            base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
            model = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
            key = ""
        if not base.startswith(("http://", "https://")):
            raise ValueError("Inference base URL must use HTTP or HTTPS")
        return cls(
            provider=provider, base_url=base, model=model, api_key=key,
            request_timeout_seconds=float(os.getenv("INFERENCE_TIMEOUT_SECONDS", "60")),
            connect_timeout_seconds=float(os.getenv("INFERENCE_CONNECT_TIMEOUT_SECONDS", "5")),
            max_concurrent_requests=_int("INFERENCE_MAX_CONCURRENT", 8, 1, 512),
            max_queue_size=_int("INFERENCE_MAX_QUEUE_SIZE", 32, 0, 4096),
            max_queue_wait_seconds=float(os.getenv("INFERENCE_QUEUE_WAIT_SECONDS", "5")),
            max_retries=_int("INFERENCE_MAX_RETRIES", 1, 0, 5),
            retry_backoff_seconds=float(os.getenv("INFERENCE_RETRY_BACKOFF_SECONDS", "0.25")),
            max_output_tokens=_int("INFERENCE_MAX_OUTPUT_TOKENS", 1024, 1, 32768),
            temperature=float(os.getenv("INFERENCE_TEMPERATURE", "0.1")),
            ollama_options={key: value for key, value in {
                "num_ctx": _optional_int("OLLAMA_NUM_CTX", 128, 131072),
                "num_batch": _optional_int("OLLAMA_NUM_BATCH", 1, 8192),
                "num_gpu": _optional_int("OLLAMA_NUM_GPU", 0, 128),
            }.items() if value is not None},
            ollama_keep_alive=os.getenv("OLLAMA_KEEP_ALIVE") or None,
        )

    def __post_init__(self):
        if self.provider not in {"ollama", "vllm"}:
            raise ValueError("provider must be ollama or vllm")
        if not self.model.strip() or not self.base_url.strip():
            raise ValueError("model and base_url are required")
        if (self.max_concurrent_requests < 1 or self.max_queue_size < 0 or
                not 0 <= self.max_retries <= 5):
            raise ValueError("invalid concurrency or retry configuration")
        numeric = (self.request_timeout_seconds, self.connect_timeout_seconds,
                   self.max_queue_wait_seconds, self.retry_backoff_seconds, self.temperature)
        if any(not math.isfinite(x) for x in numeric):
            raise ValueError("inference settings must be finite numbers")
        if self.request_timeout_seconds <= 0 or self.connect_timeout_seconds <= 0:
            raise ValueError("timeouts must be positive")
        if self.max_queue_wait_seconds < 0 or self.retry_backoff_seconds < 0 or self.max_output_tokens < 1:
            raise ValueError("invalid queue wait, retry backoff or output token limit")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if set(self.ollama_options) - {"num_ctx", "num_batch", "num_gpu"}:
            raise ValueError("unsupported Ollama option in InferenceConfig")
