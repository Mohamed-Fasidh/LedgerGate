from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

from .config import InferenceConfig


class InferenceError(RuntimeError):
    """Provider, queue, timeout, or protocol failure."""


@dataclass
class InferenceResult:
    text: str
    provider: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    time_to_first_token_ms: float | None = None
    inter_token_latency_ms: list[float] = field(default_factory=list)
    end_to_end_latency_ms: float = 0.0
    tokens_per_second: float | None = None
    request_id: str | None = None
    raw_usage: dict[str, Any] = field(default_factory=dict)


class InferenceClient:
    """Bounded synchronous client for Ollama and OpenAI-compatible vLLM.

    A bounded semaphore enforces application-side admission control. This is not
    continuous batching; the serving engine schedules admitted requests.
    """
    def __init__(self, config: InferenceConfig | None = None, *, transport=None):
        self.config = config or InferenceConfig.from_env()
        self._slots = threading.BoundedSemaphore(self.config.max_concurrent_requests)
        self._queue_lock = threading.Lock()
        self._queued = 0
        self._transport = transport

    def _client(self) -> httpx.Client:
        timeout = httpx.Timeout(
            self.config.request_timeout_seconds,
            connect=self.config.connect_timeout_seconds,
        )
        return httpx.Client(timeout=timeout, transport=self._transport, follow_redirects=False)

    def _admit(self):
        if self._slots.acquire(blocking=False):
            return
        with self._queue_lock:
            if self._queued >= self.config.max_queue_size:
                raise InferenceError("Inference queue is full; retry after capacity becomes available")
            self._queued += 1
        try:
            if not self._slots.acquire(timeout=self.config.max_queue_wait_seconds):
                raise InferenceError("Inference queue wait limit exceeded; retry later")
        finally:
            with self._queue_lock:
                self._queued -= 1

    @property
    def queue_depth(self) -> int:
        with self._queue_lock:
            return self._queued

    def _url_and_payload(self, messages: list[dict], *, stream: bool,
                         max_tokens: int | None, options: dict | None):
        if max_tokens is not None and max_tokens < 1:
            raise ValueError("max_tokens must be a positive integer")
        limit = min(max_tokens if max_tokens is not None else self.config.max_output_tokens,
                    self.config.max_output_tokens)
        if self.config.provider == "vllm":
            payload = {
                "model": self.config.model, "messages": messages,
                "temperature": self.config.temperature, "max_tokens": limit, "stream": stream,
            }
            if stream:
                payload["stream_options"] = {"include_usage": True}
            return self.config.base_url + "/chat/completions", payload
        prompt = "\n".join(
            f"{message.get('role', 'user').upper()}: {message.get('content', '')}"
            for message in messages
        )
        ollama_options = {
            "temperature": self.config.temperature,
            **self.config.ollama_options,
            "num_predict": limit,
        }
        if options:
            ollama_options.update(options)
        # A caller cannot override the configured maximum output length.
        ollama_options["num_predict"] = limit
        payload = {"model": self.config.model, "prompt": prompt, "stream": stream,
                   "options": ollama_options}
        if self.config.ollama_keep_alive:
            payload["keep_alive"] = self.config.ollama_keep_alive
        return self.config.base_url + "/api/generate", payload

    def generate(self, messages: list[dict], *, max_tokens: int | None = None,
                 options: dict | None = None) -> InferenceResult:
        self._admit()
        try:
            return self._generate_admitted(messages, max_tokens=max_tokens, options=options)
        finally:
            self._slots.release()

    def _generate_admitted(self, messages, *, max_tokens=None, options=None):
        url, payload = self._url_and_payload(
            messages, stream=False, max_tokens=max_tokens, options=options
        )
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.provider == "vllm" else {}
        started = time.perf_counter()
        last_error = None
        for attempt in range(self.config.max_retries + 1):
            try:
                with self._client() as client:
                    response = client.post(url, json=payload, headers=headers)
                    response.raise_for_status()
                    data = response.json()
                elapsed = (time.perf_counter() - started) * 1000
                if self.config.provider == "vllm":
                    choice = data.get("choices", [{}])[0]
                    content = (choice.get("message") or {}).get("content", "")
                    usage = data.get("usage") or {}
                    prompt_tokens = usage.get("prompt_tokens")
                    completion_tokens = usage.get("completion_tokens")
                else:
                    content = data.get("response", "")
                    prompt_tokens = data.get("prompt_eval_count")
                    completion_tokens = data.get("eval_count")
                    usage = {}
                rate = completion_tokens / (elapsed / 1000) if completion_tokens and elapsed > 0 else None
                total_tokens = (prompt_tokens + completion_tokens
                                if prompt_tokens is not None and completion_tokens is not None else None)
                return InferenceResult(
                    str(content), self.config.provider, self.config.model,
                    prompt_tokens, completion_tokens, total_tokens, None, [], elapsed, rate,
                    data.get("id"), usage,
                )
            except (httpx.TimeoutException, httpx.TransportError,
                    httpx.HTTPStatusError, ValueError, KeyError, IndexError) as exc:
                last_error = exc
                # Client errors are not retried; retry 429/5xx and transport failures.
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500 and exc.response.status_code != 429:
                    break
                if attempt < self.config.max_retries:
                    time.sleep(self.config.retry_backoff_seconds * (2 ** attempt))
        raise InferenceError(
            f"{self.config.provider} inference failed: {type(last_error).__name__}"
        ) from last_error

    def stream_events(self, messages: list[dict], *, max_tokens: int | None = None,
                      options: dict | None = None) -> Iterator[dict[str, Any]]:
        """Yield text/usage events; closing releases the admission slot."""
        self._admit()
        try:
            yield from self._stream_events_admitted(messages, max_tokens=max_tokens, options=options)
        finally:
            self._slots.release()

    def stream(self, messages: list[dict], *, max_tokens: int | None = None,
               options: dict | None = None) -> Iterator[str]:
        """Yield only text deltas for simple consumers."""
        events = self.stream_events(messages, max_tokens=max_tokens, options=options)
        try:
            for event in events:
                if event.get("type") == "text":
                    yield event["text"]
        finally:
            events.close()

    def _stream_events_admitted(self, messages, *, max_tokens=None, options=None):
        url, payload = self._url_and_payload(
            messages, stream=True, max_tokens=max_tokens, options=options
        )
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.provider == "vllm" else {}
        deadline = time.monotonic() + self.config.request_timeout_seconds
        try:
            with self._client() as client:
                with client.stream("POST", url, json=payload, headers=headers) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if time.monotonic() > deadline:
                            raise InferenceError("Inference stream exceeded its total request deadline")
                        if not line:
                            continue
                        if self.config.provider == "vllm":
                            if not line.startswith("data:"):
                                continue
                            data_text = line[5:].strip()
                            if data_text == "[DONE]":
                                break
                            data = json.loads(data_text)
                            choices = data.get("choices") or []
                            if choices:
                                delta = (choices[0].get("delta") or {}).get("content")
                                if delta:
                                    yield {"type": "text", "text": delta}
                            usage = data.get("usage")
                            if usage and usage.get("completion_tokens") is not None:
                                yield {
                                    "type": "usage", "prompt_tokens": usage.get("prompt_tokens"),
                                    "completion_tokens": usage.get("completion_tokens"),
                                    "total_tokens": usage.get("total_tokens"), "source": "vllm_stream_usage",
                                }
                        else:
                            data = json.loads(line)
                            delta = data.get("response")
                            if delta:
                                yield {"type": "text", "text": delta}
                            if data.get("done"):
                                if data.get("eval_count") is not None:
                                    prompt_tokens = data.get("prompt_eval_count", 0)
                                    completion_tokens = data.get("eval_count", 0)
                                    yield {
                                        "type": "usage", "prompt_tokens": prompt_tokens,
                                        "completion_tokens": completion_tokens,
                                        "total_tokens": prompt_tokens + completion_tokens,
                                        "source": "ollama_stream_usage",
                                    }
                                break
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            raise InferenceError(
                f"{self.config.provider} streaming failed: {type(exc).__name__}"
            ) from exc

    def health(self) -> dict:
        """Separate server reachability from configured model availability."""
        try:
            with self._client() as client:
                if self.config.provider == "ollama":
                    response = client.get(self.config.base_url + "/api/tags")
                    response.raise_for_status()
                    models = [item.get("name") for item in response.json().get("models", [])]
                    version = None
                    try:
                        version_response = client.get(self.config.base_url + "/api/version")
                        if version_response.is_success:
                            version = version_response.json().get("version")
                    except Exception:
                        pass
                    return {
                        "healthy": True, "provider": "ollama", "model": self.config.model,
                        "model_available": self.config.model in models, "models": models,
                        "server_version": version or os.getenv("OLLAMA_SERVER_VERSION"),
                    }
                response = client.get(
                    self.config.base_url + "/models",
                    headers={"Authorization": f"Bearer {self.config.api_key}"},
                )
                response.raise_for_status()
                models = [item.get("id") for item in response.json().get("data", [])]
                version = None
                version_url = self.config.base_url.removesuffix("/v1") + "/version"
                try:
                    version_response = client.get(
                        version_url, headers={"Authorization": f"Bearer {self.config.api_key}"}
                    )
                    if version_response.is_success:
                        version = version_response.json().get("version")
                except Exception:
                    pass
                return {
                    "healthy": True, "provider": "vllm", "model": self.config.model,
                    "model_available": self.config.model in models, "models": models,
                    "server_version": version or os.getenv("VLLM_SERVER_VERSION"),
                }
        except Exception as exc:
            return {
                "healthy": False, "provider": self.config.provider,
                "model": self.config.model, "error": type(exc).__name__,
            }
