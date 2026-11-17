"""Shared bounded inference clients and measurement tools.

Ollama and vLLM perform model execution, batching and KV-cache management. This
module handles application-side request policy and exposes server telemetry.
"""
from .client import InferenceClient, InferenceError, InferenceResult
from .config import InferenceConfig

__all__ = ["InferenceClient", "InferenceError", "InferenceResult", "InferenceConfig"]
