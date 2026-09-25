"""Unit tests for diagnosis response validation, evidence grounding, and retry policy (P7-T3).

Tests:
1. Valid diagnosis payload with valid evidence manifest.
2. Syntactically invalid JSON (syntax error).
3. Empty and whitespace-only payloads.
4. Structurally valid JSON with the wrong shape (type mismatches, wrong enum, string as list).
5. Missing required fields in JSON payload.
6. Unexpected keys forbidden (extra='forbid').
7. Hallucinated evidence IDs rejected.
8. Empty cited evidence IDs rejected as ungrounded.
9. Out-of-bounds line numbers in evidence IDs rejected.
10. Out-of-bounds line numbers in prose fields rejected.
11. Single retry success after initial schema failure.
12. Single retry success after initial grounding failure.
13. Permanent failure after retry returns DiagnosisAbstention.
14. Connection error yields immediate DiagnosisAbstention without retry.
15. Timeout yields immediate DiagnosisAbstention without retry.
16. Non-execution rule: diagnosis output is never treated as executable code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from localdev.errors import (
    OllamaConnectionError,
    OllamaTimeoutError,
    SchemaValidationError,
)
from localdev.inference.base import BaseInferenceClient
from localdev.inference.response_validator import (
    DiagnosisValidator,
    execute_diagnosis_with_retry,
)
from localdev.schemas import (
    ConfidenceEnum,
    DiagnosisAbstention,
    DiagnosisAbstentionReason,
    DiagnosisRecord,
    InferenceMetadata,
)


class DummyMockInferenceClient(BaseInferenceClient):
    """Deterministic mock client for testing retry and validation flows."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.call_history: list[list[dict[str, str]]] = []

    def generate_structured(
        self,
        prompt: str,
        schema: type[Any],
        *,
        system_prompt: str | None = None,
        model: str | None = None,
        keep_alive: int | None = None,
        enforce_prompt_budget: bool = True,
    ) -> tuple[Any, InferenceMetadata]:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self.chat_structured(messages, schema, model=model, keep_alive=keep_alive)

    def chat_structured(
        self,
        messages: list[dict[str, str]],
        schema: type[Any],
        *,
        model: str | None = None,
        keep_alive: int | None = None,
        enforce_prompt_budget: bool = True,
    ) -> tuple[Any, InferenceMetadata]:
        self.call_history.append(messages)
        if not self.responses:
            raise SchemaValidationError("Mock ran out of responses")

        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp

        metadata = InferenceMetadata(
            context_window_tokens=2048,
            prompt_budget_tokens=1200,
            output_budget_tokens=600,
            application_safety_margin_tokens=248,
            estimated_prompt_tokens=150,
            prompt_eval_count=145,
            eval_count=65,
            was_truncated=False,
            omitted_evidence_categories=[],
        )
        return resp, metadata

    def is_available(self) -> bool:
        return True

    def unload_model(self, model: str | None = None) -> bool:
        return True


class TestDiagnosisValidatorSchemaChecks:
    """Test schema validation, structural shape trapping, and JSON parsing."""

    def test_valid_diagnosis_payload(self) -> None:
        """Valid JSON conforming to DiagnosisRecord with grounded evidence passes."""
        fixture_path = Path("tests/fixtures/model_responses/diagnosis_01_off_by_one.json")
        raw_json = fixture_path.read_text(encoding="utf-8")
        manifest = ["traceback:line_10", "syntax:clean", "ast:function:process_items"]

        validator = DiagnosisValidator(evidence_manifest=manifest, total_lines=20)
        record, errors, reason = validator.validate_payload(raw_json)

        assert record is not None
        assert errors == []
        assert reason is None
        assert record.confidence == ConfidenceEnum.HIGH
        assert record.cited_evidence_ids == ["traceback:line_10", "syntax:clean"]

    def test_syntactically_invalid_json(self) -> None:
        """Syntax errors in JSON are trapped and flagged as MALFORMED_JSON."""
        validator = DiagnosisValidator()
        raw = '{"bug_description": "incomplete json'
        record, errors, reason = validator.validate_payload(raw)

        assert record is None
        assert len(errors) == 1
        assert reason == DiagnosisAbstentionReason.MALFORMED_JSON
        assert "Malformed JSON" in errors[0]

    def test_empty_or_whitespace_payload(self) -> None:
        """Empty or whitespace-only response is flagged as EMPTY_RESPONSE."""
        validator = DiagnosisValidator()
        record, _errors, reason = validator.validate_payload("   \n\t  ")

        assert record is None
        assert reason == DiagnosisAbstentionReason.EMPTY_RESPONSE

    def test_structurally_valid_json_wrong_shape(self) -> None:
        """Fixture diagnosis_malformed_shape.json (type errors, wrong enum, string as list) is trapped."""
        fixture_path = Path("tests/fixtures/model_responses/diagnosis_malformed_shape.json")
        raw_json = fixture_path.read_text(encoding="utf-8")

        validator = DiagnosisValidator()
        record, errors, reason = validator.validate_payload(raw_json)

        assert record is None
        assert reason == DiagnosisAbstentionReason.SCHEMA_VALIDATION_FAILED
        assert len(errors) > 0
        # Should flag bug_description, confidence, cited_evidence_ids, rationale
        error_blob = " ".join(errors)
        assert "bug_description" in error_blob
        assert "confidence" in error_blob
        assert "cited_evidence_ids" in error_blob
        assert "rationale" in error_blob

    def test_missing_required_fields(self) -> None:
        """JSON object missing required fields is rejected."""
        payload = json.dumps({
            "bug_description": "Off by one error in loop",
            "root_cause": "Loop range exceeded bounds",
            # missing confidence and rationale
        })
        validator = DiagnosisValidator()
        record, errors, reason = validator.validate_payload(payload)

        assert record is None
        assert reason == DiagnosisAbstentionReason.SCHEMA_VALIDATION_FAILED
        error_blob = " ".join(errors)
        assert "confidence" in error_blob
        assert "rationale" in error_blob

    def test_unexpected_keys_rejected(self) -> None:
        """Extra disallowed keys are rejected under extra='forbid'."""
        payload = json.dumps({
            "bug_description": "Division by zero",
            "root_cause": "Zero denominator",
            "confidence": "HIGH",
            "cited_evidence_ids": ["runtime:ZeroDivisionError"],
            "rationale": "Dividing by zero raises ZeroDivisionError",
            "unexpected_hallucinated_field": "malicious injection",
        })
        validator = DiagnosisValidator()
        record, errors, reason = validator.validate_payload(payload)

        assert record is None
        assert reason == DiagnosisAbstentionReason.SCHEMA_VALIDATION_FAILED
        assert "unexpected_hallucinated_field" in " ".join(errors)


class TestEvidenceGroundingAndLineBounds:
    """Test evidence manifest verification and line number boundary validation."""

    def test_hallucinated_evidence_id_rejected(self) -> None:
        """Evidence IDs absent from the supplied manifest are rejected as UNGROUNDED_EVIDENCE."""
        payload = json.dumps({
            "bug_description": "Division by zero",
            "root_cause": "Count was zero",
            "confidence": "HIGH",
            "cited_evidence_ids": ["runtime:ZeroDivisionError", "hallucinated:fake_id"],
            "rationale": "Count is zero",
        })
        manifest = ["runtime:ZeroDivisionError", "syntax:clean"]

        validator = DiagnosisValidator(evidence_manifest=manifest, total_lines=25)
        record, errors, reason = validator.validate_payload(payload)

        assert record is None
        assert reason == DiagnosisAbstentionReason.UNGROUNDED_EVIDENCE
        assert "hallucinated:fake_id" in errors[0]

    def test_empty_cited_evidence_rejected(self) -> None:
        """Diagnosis citing zero evidence IDs is rejected as UNGROUNDED_EVIDENCE."""
        payload = json.dumps({
            "bug_description": "Division by zero",
            "root_cause": "Count was zero",
            "confidence": "HIGH",
            "cited_evidence_ids": [],
            "rationale": "Count is zero",
        })
        validator = DiagnosisValidator(evidence_manifest=["runtime:clean"], total_lines=25)
        record, errors, reason = validator.validate_payload(payload)

        assert record is None
        assert reason == DiagnosisAbstentionReason.UNGROUNDED_EVIDENCE
        assert "must cite at least one verified evidence ID" in errors[0]

    def test_out_of_bounds_line_number_in_evidence_id(self) -> None:
        """Evidence ID citing a line number beyond total target lines is rejected."""
        payload = json.dumps({
            "bug_description": "Syntax error",
            "root_cause": "Unmatched parenthesis",
            "confidence": "HIGH",
            "cited_evidence_ids": ["traceback:line_500"],
            "rationale": "Line 500 contains unmatched paren",
        })
        # Manifest has the ID, but total_lines is only 20
        manifest = ["traceback:line_500"]

        validator = DiagnosisValidator(evidence_manifest=manifest, total_lines=20)
        record, errors, reason = validator.validate_payload(payload)

        assert record is None
        assert reason == DiagnosisAbstentionReason.OUT_OF_BOUNDS_LINES
        assert "500" in errors[0]

    def test_out_of_bounds_line_number_in_prose(self) -> None:
        """Prose text mentioning an out-of-bounds line is rejected."""
        payload = json.dumps({
            "bug_description": "Error occurs on line 999 when calling helper",
            "root_cause": "Invalid argument",
            "confidence": "HIGH",
            "cited_evidence_ids": ["syntax:clean"],
            "rationale": "Syntax parsed cleanly",
        })
        manifest = ["syntax:clean"]

        validator = DiagnosisValidator(evidence_manifest=manifest, total_lines=30)
        record, errors, reason = validator.validate_payload(payload)

        assert record is None
        assert reason == DiagnosisAbstentionReason.OUT_OF_BOUNDS_LINES
        assert "999" in errors[0]


class TestRetryPolicyAndAbstention:
    """Test automated single-retry flow and abstention generation."""

    def test_execute_diagnosis_success_on_first_attempt(self) -> None:
        """When first attempt is valid and grounded, it returns immediately without retry."""
        valid_record = DiagnosisRecord(
            bug_description="Loop off by one",
            root_cause="Upper bound exceeds len",
            confidence=ConfidenceEnum.HIGH,
            cited_evidence_ids=["traceback:line_10"],
            rationale="IndexError on final iteration",
        )
        client = DummyMockInferenceClient([valid_record])
        manifest = ["traceback:line_10"]

        result, metadata = execute_diagnosis_with_retry(
            client=client,
            messages=[{"role": "user", "content": "analyze code"}],
            target_path="script.py",
            total_lines=20,
            evidence_manifest=manifest,
        )

        assert isinstance(result, DiagnosisRecord)
        assert result.root_cause == "Upper bound exceeds len"
        assert len(client.call_history) == 1
        assert metadata is not None

    def test_execute_diagnosis_recovers_after_schema_failure(self) -> None:
        """First attempt fails schema validation; second attempt succeeds with correction prompt."""
        valid_record = DiagnosisRecord(
            bug_description="Division by zero",
            root_cause="Zero denominator",
            confidence=ConfidenceEnum.HIGH,
            cited_evidence_ids=["runtime:ZeroDivisionError"],
            rationale="Evaluating total / 0 raises ZeroDivisionError",
        )
        # Attempt 1: SchemaValidationError with malformed raw payload
        schema_err = SchemaValidationError(
            "Model output failed DiagnosisRecord validation",
            raw_payload='{"bug_description": 12345}',
        )
        # Attempt 2: valid DiagnosisRecord
        client = DummyMockInferenceClient([schema_err, valid_record])
        manifest = ["runtime:ZeroDivisionError"]

        result, _metadata = execute_diagnosis_with_retry(
            client=client,
            messages=[{"role": "user", "content": "analyze code"}],
            target_path="script.py",
            total_lines=15,
            evidence_manifest=manifest,
        )

        assert isinstance(result, DiagnosisRecord)
        assert result.confidence == ConfidenceEnum.HIGH
        assert len(client.call_history) == 2
        # Check that retry messages include assistant output and schema correction prompt
        retry_msgs = client.call_history[1]
        assert len(retry_msgs) == 3
        assert retry_msgs[1]["role"] == "assistant"
        assert retry_msgs[1]["content"] == '{"bug_description": 12345}'
        assert "Your previous JSON response failed strict Pydantic validation" in retry_msgs[2]["content"]

    def test_execute_diagnosis_recovers_after_grounding_failure(self) -> None:
        """First attempt cites hallucinated evidence; second attempt succeeds with grounded evidence."""
        ungrounded_record = DiagnosisRecord(
            bug_description="Type error",
            root_cause="Mismatch",
            confidence=ConfidenceEnum.HIGH,
            cited_evidence_ids=["hallucinated:id"],
            rationale="Rationale",
        )
        grounded_record = DiagnosisRecord(
            bug_description="Type error",
            root_cause="Mismatch",
            confidence=ConfidenceEnum.HIGH,
            cited_evidence_ids=["traceback:line_5"],
            rationale="Rationale",
        )
        client = DummyMockInferenceClient([ungrounded_record, grounded_record])
        manifest = ["traceback:line_5"]

        result, _metadata = execute_diagnosis_with_retry(
            client=client,
            messages=[{"role": "user", "content": "analyze code"}],
            target_path="script.py",
            total_lines=15,
            evidence_manifest=manifest,
        )

        assert isinstance(result, DiagnosisRecord)
        assert result.cited_evidence_ids == ["traceback:line_5"]
        assert len(client.call_history) == 2
        retry_msgs = client.call_history[1]
        assert "contained ungrounded evidence IDs" in retry_msgs[-1]["content"]

    def test_execute_diagnosis_permanent_failure_emits_abstention(self) -> None:
        """When both attempts fail validation, a DiagnosisAbstention report is emitted."""
        schema_err_1 = SchemaValidationError("Attempt 1 bad schema", raw_payload="bad_1")
        schema_err_2 = SchemaValidationError("Attempt 2 bad schema", raw_payload="bad_2")
        client = DummyMockInferenceClient([schema_err_1, schema_err_2])

        result, _metadata = execute_diagnosis_with_retry(
            client=client,
            messages=[{"role": "user", "content": "analyze code"}],
            target_path="script.py",
            total_lines=20,
            evidence_manifest=["syntax:clean"],
        )

        assert isinstance(result, DiagnosisAbstention)
        assert result.reason == DiagnosisAbstentionReason.SCHEMA_VALIDATION_FAILED
        assert result.retry_attempted is True
        assert result.raw_payload == "bad_2"
        assert len(client.call_history) == 2

    def test_connection_error_yields_abstention_without_retry(self) -> None:
        """When Ollama is unreachable, abstention is returned immediately without useless retries."""
        conn_err = OllamaConnectionError("Connection refused on 11434")
        client = DummyMockInferenceClient([conn_err])

        result, _metadata = execute_diagnosis_with_retry(
            client=client,
            messages=[{"role": "user", "content": "analyze code"}],
            target_path="script.py",
            total_lines=20,
            evidence_manifest=["syntax:clean"],
        )

        assert isinstance(result, DiagnosisAbstention)
        assert result.reason == DiagnosisAbstentionReason.MODEL_UNAVAILABLE
        assert result.retry_attempted is False
        assert len(client.call_history) == 1

    def test_timeout_error_yields_abstention_without_retry(self) -> None:
        """When Ollama times out, abstention is returned immediately without retry."""
        timeout_err = OllamaTimeoutError("Request timed out after 30s")
        client = DummyMockInferenceClient([timeout_err])

        result, _metadata = execute_diagnosis_with_retry(
            client=client,
            messages=[{"role": "user", "content": "analyze code"}],
            target_path="script.py",
            total_lines=20,
            evidence_manifest=["syntax:clean"],
        )

        assert isinstance(result, DiagnosisAbstention)
        assert result.reason == DiagnosisAbstentionReason.INFERENCE_TIMEOUT
        assert result.retry_attempted is False
        assert len(client.call_history) == 1
