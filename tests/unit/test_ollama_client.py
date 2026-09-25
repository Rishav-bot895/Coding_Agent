"""Unit tests for the Ollama inference client (P7-T1).

Verifies:
- Offline verification / rejection of non-local endpoints.
- Request options enforcing separated token budgets (num_ctx: 2048, num_predict: 600).
- Pydantic model JSON Schema passed in 'format' parameter.
- Application-enforced prompt budget (<= 1,200 tokens).
- Application-side strict Pydantic validation (rejecting malformed/invalid output).
- Usage metrics capture (prompt_eval_count, eval_count, durations).
- Model lifecycle management (unload_model with keep_alive: 0).
- Error handling for connection refused, timeout, 404 model not found, and 400 context exceeded.
- Non-execution invariant (pure data return without execution).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from localdev.config import LocaldevConfig
from localdev.constants import (
    APPLICATION_SAFETY_MARGIN_TOKENS,
    CONTEXT_WINDOW_TOKENS,
    OUTPUT_BUDGET_TOKENS,
    PROMPT_BUDGET_TOKENS,
)
from localdev.errors import (
    InferenceError,
    NonLocalUrlError,
    OllamaConnectionError,
    OllamaContextExceededError,
    OllamaModelNotFoundError,
    OllamaTimeoutError,
    PromptBudgetExceededError,
    SchemaValidationError,
)
from localdev.inference.ollama_client import OllamaClient, validate_local_endpoint
from localdev.schemas import ConfidenceEnum, DiagnosisRecord, EditProposalRecord

# =============================================================================
# Offline Verification / Local Endpoint Tests
# =============================================================================


def test_validate_local_endpoint_allowed() -> None:
    """Validate that local loopback URLs are accepted."""
    allowed = [
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://127.0.0.1:8000",
        "http://[::1]:11434",
        "https://127.0.0.1:11434",
    ]
    for url in allowed:
        validate_local_endpoint(url)  # Must not raise


def test_validate_local_endpoint_rejected() -> None:
    """Validate that external or non-loopback URLs are strictly rejected."""
    rejected = [
        "https://api.openai.com/v1",
        "http://192.168.1.5:11434",
        "http://10.0.0.1:11434",
        "http://example.com/ollama",
        "ftp://127.0.0.1:11434",
        "",
        "   ",
    ]
    for url in rejected:
        with pytest.raises(NonLocalUrlError):
            validate_local_endpoint(url)


def test_client_init_rejects_external_url() -> None:
    """OllamaClient raises NonLocalUrlError if config contains an external URL."""
    cfg = LocaldevConfig(ollama_url="https://api.openai.com")
    with pytest.raises(NonLocalUrlError):
        OllamaClient(config=cfg)


# =============================================================================
# Request Payload and Token Budgeting Tests
# =============================================================================


def test_separated_token_budgets_and_options_in_request_payload() -> None:
    """Request payload to Ollama strictly sets num_ctx: 2048, num_predict: 600, and JSON Schema format."""
    recorded_requests: list[httpx.Request] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_requests.append(request)
        response_body = {
            "model": "qwen2.5-coder:3b-instruct-q4_K_M",
            "message": {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "bug_description": "Off-by-one boundary index error",
                        "root_cause": "Loop upper bound inclusive instead of exclusive",
                        "confidence": "HIGH",
                        "cited_evidence_ids": ["SIG-1", "TB-2"],
                        "rationale": "Traceback frame 2 index exceeds length.",
                    }
                ),
            },
            "done": True,
            "prompt_eval_count": 210,
            "eval_count": 65,
            "prompt_eval_duration": 120000000,
            "eval_duration": 350000000,
        }
        return httpx.Response(200, json=response_body)

    transport = httpx.MockTransport(mock_handler)
    http_client = httpx.Client(transport=transport, base_url="http://127.0.0.1:11434")

    client = OllamaClient(http_client=http_client)
    record, metadata = client.generate_structured(
        prompt="Sample evidence prompt",
        schema=DiagnosisRecord,
        system_prompt="Analyze the bug",
    )
    assert record.bug_description
    assert metadata.prompt_eval_count == 210

    assert len(recorded_requests) == 1
    req = recorded_requests[0]
    payload = json.loads(req.content.decode("utf-8"))

    # Verify options strictly enforce separated token budgets
    options = payload["options"]
    assert options["num_ctx"] == CONTEXT_WINDOW_TOKENS  # 2048
    assert options["num_predict"] == OUTPUT_BUDGET_TOKENS  # 600

    # Verify format parameter passes Pydantic JSON Schema dictionary
    assert payload["format"] == DiagnosisRecord.model_json_schema()
    assert payload["stream"] is False
    assert payload["keep_alive"] == 0

    # Verify messages structure
    assert len(payload["messages"]) == 2
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][0]["content"] == "Analyze the bug"
    assert payload["messages"][1]["role"] == "user"
    assert payload["messages"][1]["content"] == "Sample evidence prompt"


def test_application_enforced_prompt_budget() -> None:
    """Client raises PromptBudgetExceededError when prompt exceeds prompt_budget_tokens (1,200)."""
    client = OllamaClient()

    # Prompt with 5,000 characters (~1,600+ tokens, exceeding 1,200 limit)
    oversized_prompt = "x" * 5000

    with pytest.raises(PromptBudgetExceededError) as exc_info:
        client.generate_structured(
            prompt=oversized_prompt,
            schema=DiagnosisRecord,
            enforce_prompt_budget=True,
        )

    err = exc_info.value
    assert err.estimated_tokens > PROMPT_BUDGET_TOKENS
    assert err.prompt_budget == PROMPT_BUDGET_TOKENS
    assert "exceeds the prompt budget" in str(err)


# =============================================================================
# Response Validation and Metrics Tests
# =============================================================================


def test_successful_structured_diagnosis_response() -> None:
    """Client validates response into DiagnosisRecord and captures Ollama usage metrics."""
    diag_data = {
        "bug_description": "Division by zero on empty input",
        "root_cause": "Divisor evaluated to 0 when input array is empty",
        "confidence": "HIGH",
        "cited_evidence_ids": ["E1"],
        "rationale": "Zero division verified via runtime evidence.",
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "qwen2.5-coder:3b-instruct-q4_K_M",
                "message": {"role": "assistant", "content": json.dumps(diag_data)},
                "prompt_eval_count": 300,
                "eval_count": 75,
                "prompt_eval_duration": 150000000,  # 150ms
                "eval_duration": 420000000,  # 420ms
            },
        )

    client = OllamaClient(http_client=httpx.Client(transport=httpx.MockTransport(mock_handler)))
    record, metadata = client.generate_structured("Analyze code", DiagnosisRecord)

    # 1. Pydantic validation
    assert isinstance(record, DiagnosisRecord)
    assert record.bug_description == "Division by zero on empty input"
    assert record.confidence == ConfidenceEnum.HIGH

    # 2. Usage metrics and budget partition
    assert metadata.context_window_tokens == CONTEXT_WINDOW_TOKENS  # 2048
    assert metadata.prompt_budget_tokens == PROMPT_BUDGET_TOKENS  # 1200
    assert metadata.output_budget_tokens == OUTPUT_BUDGET_TOKENS  # 600
    assert metadata.application_safety_margin_tokens == APPLICATION_SAFETY_MARGIN_TOKENS  # 248
    assert metadata.prompt_eval_count == 300
    assert metadata.eval_count == 75
    assert metadata.prompt_eval_duration_ms == 150.0
    assert metadata.eval_duration_ms == 420.0
    assert metadata.was_truncated is False


def test_successful_structured_edit_proposal_response() -> None:
    """Client validates response into EditProposalRecord."""
    edit_data = {
        "target_file": "script.py",
        "edits": [
            {
                "operation": "replace",
                "start_line": 5,
                "end_line": 5,
                "expected_text": "return a / b",
                "replacement_text": "return a / b if b != 0 else 0",
            }
        ],
        "explanation": "Guard against zero division.",
    }

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": json.dumps(edit_data)},
                "prompt_eval_count": 100,
                "eval_count": 40,
            },
        )

    client = OllamaClient(http_client=httpx.Client(transport=httpx.MockTransport(mock_handler)))
    proposal, metadata = client.generate_structured("Propose edit", EditProposalRecord)

    assert isinstance(proposal, EditProposalRecord)
    assert proposal.target_file == "script.py"
    assert len(proposal.edits) == 1
    assert proposal.edits[0].replacement_text == "return a / b if b != 0 else 0"
    assert metadata.prompt_eval_count == 100
    assert metadata.eval_count == 40


def test_application_side_pydantic_validation_failures() -> None:
    """Invalid, incomplete, or non-JSON model output triggers SchemaValidationError."""
    test_cases: list[dict[str, Any]] = [
        # 1. Non-JSON string
        {"message": {"role": "assistant", "content": "I am unable to fix this bug."}},
        # 2. Missing required field 'root_cause'
        {"message": {"role": "assistant", "content": json.dumps({"bug_description": "broken"})}},
        # 3. Invalid confidence enum value
        {
            "message": {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "bug_description": "broken",
                        "root_cause": "bug",
                        "confidence": "INVALID_CONFIDENCE",
                        "rationale": "why",
                    }
                ),
            }
        },
        # 4. Empty content
        {"message": {"role": "assistant", "content": ""}},
    ]

    for body in test_cases:

        def mock_handler(request: httpx.Request, current_body: dict[str, Any] = body) -> httpx.Response:
            return httpx.Response(200, json=current_body)

        client = OllamaClient(http_client=httpx.Client(transport=httpx.MockTransport(mock_handler)))
        with pytest.raises(SchemaValidationError):
            client.generate_structured("prompt", DiagnosisRecord)


# =============================================================================
# Error Handling Tests
# =============================================================================


def test_error_handling_connection_refused() -> None:
    """Unreachable Ollama server raises OllamaConnectionError."""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    client = OllamaClient(http_client=httpx.Client(transport=httpx.MockTransport(mock_handler)))
    with pytest.raises(OllamaConnectionError) as exc_info:
        client.generate_structured("prompt", DiagnosisRecord)

    assert "Ensure Ollama is started" in str(exc_info.value)


def test_error_handling_timeout() -> None:
    """Timeout during Ollama inference raises OllamaTimeoutError."""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Read timed out")

    client = OllamaClient(http_client=httpx.Client(transport=httpx.MockTransport(mock_handler)))
    with pytest.raises(OllamaTimeoutError) as exc_info:
        client.generate_structured("prompt", DiagnosisRecord)

    assert "timed out" in str(exc_info.value)


def test_error_handling_model_not_found_404() -> None:
    """HTTP 404 raises OllamaModelNotFoundError indicating model needs to be pulled."""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model 'qwen2.5-coder:3b-instruct-q4_K_M' not found"})

    client = OllamaClient(http_client=httpx.Client(transport=httpx.MockTransport(mock_handler)))
    with pytest.raises(OllamaModelNotFoundError) as exc_info:
        client.generate_structured("prompt", DiagnosisRecord)

    assert "Pull it with 'ollama pull" in str(exc_info.value)


def test_error_handling_context_exceeded_400() -> None:
    """HTTP 400 with context error raises OllamaContextExceededError."""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "context size 2048 exceeded by input"})

    client = OllamaClient(http_client=httpx.Client(transport=httpx.MockTransport(mock_handler)))
    with pytest.raises(OllamaContextExceededError):
        client.generate_structured("prompt", DiagnosisRecord)


def test_error_handling_server_error_500() -> None:
    """HTTP 500 raises general InferenceError."""

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="Internal server error")

    client = OllamaClient(http_client=httpx.Client(transport=httpx.MockTransport(mock_handler)))
    with pytest.raises(InferenceError) as exc_info:
        client.generate_structured("prompt", DiagnosisRecord)

    assert "HTTP 500" in str(exc_info.value)


# =============================================================================
# Model Lifecycle and Utility Tests
# =============================================================================


def test_model_lifecycle_unload_and_is_available() -> None:
    """unload_model sends keep_alive: 0 and is_available queries /api/tags."""
    recorded_endpoints: list[str] = []
    recorded_payloads: list[dict[str, Any]] = []

    def mock_handler(request: httpx.Request) -> httpx.Response:
        recorded_endpoints.append(request.url.path)
        if request.url.path == "/api/generate":
            recorded_payloads.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(200, json={"done": True})
        if request.url.path == "/api/tags":
            return httpx.Response(
                200,
                json={"models": [{"name": "qwen2.5-coder:3b-instruct-q4_K_M"}]},
            )
        return httpx.Response(404)

    client = OllamaClient(http_client=httpx.Client(transport=httpx.MockTransport(mock_handler)))

    # is_available
    assert client.is_available() is True
    assert "/api/tags" in recorded_endpoints

    # list_models
    models = client.list_models()
    assert models == ["qwen2.5-coder:3b-instruct-q4_K_M"]

    # unload_model
    success = client.unload_model()
    assert success is True
    assert recorded_payloads[0]["keep_alive"] == 0
    assert recorded_payloads[0]["model"] == "qwen2.5-coder:3b-instruct-q4_K_M"


def test_client_context_manager() -> None:
    """OllamaClient can be used as a context manager."""
    with OllamaClient() as client:
        assert isinstance(client, OllamaClient)
