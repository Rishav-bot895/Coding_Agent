"""Local SLM inference clients and abstractions."""

from __future__ import annotations

from localdev.inference.base import BaseInferenceClient
from localdev.inference.ollama_client import OllamaClient, validate_local_endpoint

__all__ = [
    "BaseInferenceClient",
    "OllamaClient",
    "validate_local_endpoint",
]

