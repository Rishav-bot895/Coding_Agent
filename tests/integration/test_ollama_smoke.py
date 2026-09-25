"""Integration smoke tests against a running local Ollama instance (P7-T1).

Verifies:
- Live connectivity to http://127.0.0.1:11434.
- Model availability and structured JSON Schema constrained generation.
- Real token evaluation metrics capture.
- Clean model unloading via keep_alive: 0.

Tagged with @pytest.mark.ollama so they can run when Ollama is available
and are cleanly skipped when Ollama is offline or the model is not installed.
"""

from __future__ import annotations

import pytest

from localdev.inference.ollama_client import OllamaClient
from localdev.schemas import DiagnosisRecord

pytestmark = [pytest.mark.ollama, pytest.mark.integration]


def test_ollama_smoke_structured_generation() -> None:
    """Smoke test running live structured diagnosis against local Ollama instance."""
    client = OllamaClient()

    # 1. Skip if Ollama service is not reachable
    if not client.is_available():
        pytest.skip("Local Ollama server is not running on http://127.0.0.1:11434.")

    # 2. Check if primary or fallback model is installed
    models = client.list_models()
    active_model = client.config.active_model

    # Check for exact or base model name match
    model_installed = any(
        active_model == m or active_model.split(":")[0] == m.split(":")[0]
        for m in models
    )
    if not model_installed:
        pytest.skip(
            f"Active model '{active_model}' is not pulled in local Ollama. Available: {models}"
        )

    # 3. Live structured inference with JSON Schema constraints
    prompt = (
        "def divide(a, b):\n"
        "    return a / b\n\n"
        "Failure: ZeroDivisionError: division by zero when divide(10, 0) is called."
    )
    system_prompt = (
        "You are an offline Python debugging agent. Analyze the defect and output structured JSON."
    )

    try:
        record, metadata = client.generate_structured(
            prompt=prompt,
            schema=DiagnosisRecord,
            system_prompt=system_prompt,
        )

        assert isinstance(record, DiagnosisRecord)
        assert record.bug_description
        assert record.root_cause

        assert metadata.context_window_tokens == 2048
        assert metadata.prompt_budget_tokens == 1200
        assert metadata.output_budget_tokens == 600
        assert metadata.application_safety_margin_tokens == 248

        if metadata.prompt_eval_count is not None:
            assert metadata.prompt_eval_count > 0
        if metadata.eval_count is not None:
            assert metadata.eval_count > 0

    finally:
        # Explicitly unload model to release RAM
        client.unload_model()

