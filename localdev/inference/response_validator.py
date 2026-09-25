"""Response validation, evidence grounding verification, and retry policy for SLM diagnosis.

Enforces:
- Application-side Pydantic validation against DiagnosisRecord with strict type checking (extra='forbid').
- Explicit trapping and rejection of structurally valid JSON with the wrong shape (e.g. integer where
  string expected, string instead of array, missing required keys, or unexpected keys).
- Evidence grounding verification: every cited evidence ID must exist in the supplied evidence manifest.
- Line range verification: cited lines must fall within target file line bounds (1 to total_lines).
- Single automated retry policy: if model response is malformed JSON, schema-invalid, ungrounded, or
  out-of-bounds, retry exactly once with an explicit correction prompt.
- Safe abstention: if retry fails, emit an explicit DiagnosisAbstention report.
- Non-execution rule: model diagnosis prose and proposals are informational structures and are never
  treated as executable commands.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from localdev.errors import (
    InferenceError,
    OllamaConnectionError,
    OllamaTimeoutError,
    PromptBudgetExceededError,
    SchemaValidationError,
)
from localdev.inference.base import BaseInferenceClient
from localdev.inference.prompts import (
    build_grounding_correction_prompt,
    build_schema_correction_prompt,
)
from localdev.patching import EditProposalRecord, EditValidator
from localdev.schemas import (
    DiagnosisAbstention,
    DiagnosisAbstentionReason,
    DiagnosisRecord,
    InferenceMetadata,
)

# Regex to detect explicit line number references in text (e.g., 'line 42', 'lines 10-20')
LINE_REF_PATTERN = re.compile(r"\b(?:line|lines)\s+(\d+)(?:\s*(?:to|-)\s*(\d+))?\b", re.IGNORECASE)
# Regex to detect line numbers inside evidence IDs (e.g. 'traceback:line_10', 'ruff:F841:line_5', 'source:lines_1-10')
EVIDENCE_LINE_PATTERN = re.compile(r"(?:line_|lines_|L)(\d+)(?:-(\d+))?")


class DiagnosisValidator:
    """Validates raw model output against DiagnosisRecord schema, evidence grounding, and line bounds."""

    def __init__(
        self,
        evidence_manifest: Sequence[str] | set[str] | None = None,
        total_lines: int | None = None,
        require_at_least_one_evidence: bool = True,
    ) -> None:
        self.evidence_manifest = set(evidence_manifest) if evidence_manifest is not None else set()
        self.total_lines = total_lines
        self.require_at_least_one_evidence = require_at_least_one_evidence

    def validate_payload(
        self,
        raw_payload: str,
    ) -> tuple[DiagnosisRecord | None, list[str], DiagnosisAbstentionReason | None]:
        """Validate raw JSON payload string against schema, grounding, and line limits.

        Returns:
            Tuple of (record, error_list, abstention_reason).
            If valid, record is returned with empty error list and None reason.
        """
        if not raw_payload or not raw_payload.strip():
            return None, ["Model output is empty or whitespace."], DiagnosisAbstentionReason.EMPTY_RESPONSE

        # 1. Check valid JSON syntax
        try:
            parsed_json: Any = json.loads(raw_payload)
        except (ValueError, json.JSONDecodeError) as exc:
            return (
                None,
                [f"Malformed JSON syntax: {exc}"],
                DiagnosisAbstentionReason.MALFORMED_JSON,
            )

        if not isinstance(parsed_json, dict):
            return (
                None,
                [f"Expected JSON object (dict), got {type(parsed_json).__name__}."],
                DiagnosisAbstentionReason.SCHEMA_VALIDATION_FAILED,
            )

        # 2. Strict application-side Pydantic validation (trapping structural shape mismatches)
        try:
            record = DiagnosisRecord.model_validate(parsed_json)
        except ValidationError as exc:
            error_msgs = [
                f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']} (got {err.get('input', 'missing')!r})"
                for err in exc.errors()
            ]
            return (
                None,
                error_msgs,
                DiagnosisAbstentionReason.SCHEMA_VALIDATION_FAILED,
            )

        # 3. Grounding and line verification on validated record
        is_valid, errors, reason = self.validate_record(record)
        if not is_valid:
            return None, errors, reason

        return record, [], None

    def validate_record(
        self,
        record: DiagnosisRecord,
    ) -> tuple[bool, list[str], DiagnosisAbstentionReason | None]:
        """Verify evidence grounding and line number bounds on an already-parsed DiagnosisRecord."""
        errors: list[str] = []

        # 1. Evidence grounding check
        if self.require_at_least_one_evidence and not record.cited_evidence_ids:
            errors.append("Diagnosis must cite at least one verified evidence ID from the manifest.")
            return False, errors, DiagnosisAbstentionReason.UNGROUNDED_EVIDENCE

        if self.evidence_manifest:
            hallucinated_ids = [
                eid for eid in record.cited_evidence_ids if eid not in self.evidence_manifest
            ]
            if hallucinated_ids:
                errors.append(
                    f"Cited evidence IDs not found in Available Evidence Manifest: {hallucinated_ids}. "
                    "All cited IDs must strictly match provided manifest."
                )
                return False, errors, DiagnosisAbstentionReason.UNGROUNDED_EVIDENCE

        # 2. Line number bounds check
        if self.total_lines is not None and self.total_lines > 0:
            out_of_bounds: list[int] = []

            # Check lines inside cited evidence IDs
            for eid in record.cited_evidence_ids:
                for match in EVIDENCE_LINE_PATTERN.finditer(eid):
                    l1 = int(match.group(1))
                    if l1 < 1 or l1 > self.total_lines:
                        out_of_bounds.append(l1)
                    if match.group(2):
                        l2 = int(match.group(2))
                        if l2 < 1 or l2 > self.total_lines:
                            out_of_bounds.append(l2)

            # Check explicit line references in prose fields
            prose_texts = [record.bug_description, record.root_cause, record.rationale]
            for text in prose_texts:
                for match in LINE_REF_PATTERN.finditer(text):
                    l1 = int(match.group(1))
                    if l1 < 1 or l1 > self.total_lines:
                        out_of_bounds.append(l1)
                    if match.group(2):
                        l2 = int(match.group(2))
                        if l2 < 1 or l2 > self.total_lines:
                            out_of_bounds.append(l2)

            if out_of_bounds:
                unique_oob = sorted(set(out_of_bounds))
                errors.append(
                    f"Diagnosis cites out-of-bounds line numbers {unique_oob}. "
                    f"Target file has only {self.total_lines} lines (valid range: 1 to {self.total_lines})."
                )
                return False, errors, DiagnosisAbstentionReason.OUT_OF_BOUNDS_LINES

        return True, [], None


def execute_diagnosis_with_retry(
    client: BaseInferenceClient,
    messages: list[dict[str, str]],
    target_path: str,
    total_lines: int,
    evidence_manifest: Sequence[str] | set[str],
    *,
    model: str | None = None,
    keep_alive: int | None = 0,
) -> tuple[DiagnosisRecord | DiagnosisAbstention, InferenceMetadata | None]:
    """Execute diagnosis inference with strict Pydantic validation, grounding checks, and single retry.

    Workflow:
    1. First attempt: call client.chat_structured with messages.
    2. If successful, validate evidence grounding and line numbers.
       If valid: return (DiagnosisRecord, metadata).
       If invalid: construct grounding correction prompt and proceed to retry.
    3. If first attempt fails schema validation:
       Catch SchemaValidationError and construct schema correction prompt.
    4. If first attempt encounters connection error or timeout:
       Safely return DiagnosisAbstention without useless network retries.
    5. Second attempt (retry): send conversation history with correction prompt.
    6. Validate retry output:
       If valid: return (DiagnosisRecord, retry_metadata).
       If invalid: return DiagnosisAbstention detailing failure reasons.
    """
    validator = DiagnosisValidator(
        evidence_manifest=evidence_manifest,
        total_lines=total_lines,
        require_at_least_one_evidence=True,
    )

    first_attempt_raw: str | None = None
    first_attempt_errors: list[str] = []
    first_attempt_reason: DiagnosisAbstentionReason | None = None
    correction_prompt: str = ""

    # -------------------------------------------------------------------------
    # Attempt 1
    # -------------------------------------------------------------------------
    try:
        candidate_record, metadata = client.chat_structured(
            messages,
            DiagnosisRecord,
            model=model,
            keep_alive=keep_alive,
        )
        # Verify evidence grounding and line bounds on parsed record
        is_valid, errors, reason = validator.validate_record(candidate_record)
        if is_valid:
            return candidate_record, metadata

        first_attempt_raw = candidate_record.model_dump_json()
        first_attempt_errors = errors
        first_attempt_reason = reason
        correction_prompt = build_grounding_correction_prompt(
            validation_errors=errors,
            available_manifest=list(evidence_manifest),
            raw_payload=first_attempt_raw,
        )

    except SchemaValidationError as exc:
        first_attempt_raw = exc.raw_payload
        first_attempt_errors = [str(exc)]
        first_attempt_reason = DiagnosisAbstentionReason.SCHEMA_VALIDATION_FAILED
        correction_prompt = build_schema_correction_prompt(
            error_message=str(exc),
            raw_payload=first_attempt_raw or "",
            schema_cls=DiagnosisRecord,
        )

    except OllamaConnectionError as exc:
        return (
            DiagnosisAbstention(
                target=target_path,
                reason=DiagnosisAbstentionReason.MODEL_UNAVAILABLE,
                details=f"Ollama inference service unavailable: {exc}",
                validation_errors=[str(exc)],
                retry_attempted=False,
            ),
            None,
        )

    except OllamaTimeoutError as exc:
        return (
            DiagnosisAbstention(
                target=target_path,
                reason=DiagnosisAbstentionReason.INFERENCE_TIMEOUT,
                details=f"Ollama inference timed out: {exc}",
                validation_errors=[str(exc)],
                retry_attempted=False,
            ),
            None,
        )

    except PromptBudgetExceededError as exc:
        return (
            DiagnosisAbstention(
                target=target_path,
                reason=DiagnosisAbstentionReason.PROMPT_BUDGET_EXCEEDED,
                details=f"Prompt budget exceeded: {exc}",
                validation_errors=[str(exc)],
                retry_attempted=False,
            ),
            None,
        )

    except InferenceError as exc:
        return (
            DiagnosisAbstention(
                target=target_path,
                reason=DiagnosisAbstentionReason.MODEL_UNAVAILABLE,
                details=f"Inference error: {exc}",
                validation_errors=[str(exc)],
                retry_attempted=False,
            ),
            None,
        )

    # -------------------------------------------------------------------------
    # Attempt 2 (Single Automated Retry)
    # -------------------------------------------------------------------------
    retry_messages = list(messages)
    if first_attempt_raw:
        retry_messages.append({"role": "assistant", "content": first_attempt_raw})
    retry_messages.append({"role": "user", "content": correction_prompt})

    try:
        retry_record, retry_metadata = client.chat_structured(
            retry_messages,
            DiagnosisRecord,
            model=model,
            keep_alive=keep_alive,
        )
        # Validate grounding and line numbers on retry record
        is_valid, errors, reason = validator.validate_record(retry_record)
        if is_valid:
            return retry_record, retry_metadata

        return (
            DiagnosisAbstention(
                target=target_path,
                reason=reason or DiagnosisAbstentionReason.UNGROUNDED_EVIDENCE,
                details=f"Retry response failed validation: {'; '.join(errors)}",
                raw_payload=retry_record.model_dump_json(),
                validation_errors=errors,
                retry_attempted=True,
            ),
            retry_metadata,
        )

    except SchemaValidationError as exc:
        return (
            DiagnosisAbstention(
                target=target_path,
                reason=DiagnosisAbstentionReason.SCHEMA_VALIDATION_FAILED,
                details=f"Retry response failed schema validation: {exc}",
                raw_payload=exc.raw_payload,
                validation_errors=[str(exc)],
                retry_attempted=True,
            ),
            None,
        )

    except InferenceError as exc:
        return (
            DiagnosisAbstention(
                target=target_path,
                reason=first_attempt_reason or DiagnosisAbstentionReason.MODEL_UNAVAILABLE,
                details=f"Inference error during retry: {exc}",
                raw_payload=first_attempt_raw,
                validation_errors=first_attempt_errors + [str(exc)],
                retry_attempted=True,
            ),
            None,
        )


class EditProposalValidator:
    """Validates raw model output or parsed EditProposalRecord against schema, line bounds, and target source."""

    def __init__(
        self,
        target: Any,
        source_text: str,
    ) -> None:
        self.target = target
        self.source_text = source_text
        self.edit_validator = EditValidator(target=target, source_text=source_text)

    def validate_payload(
        self,
        raw_payload: str,
    ) -> tuple[EditProposalRecord | None, list[str]]:
        """Validate raw JSON payload string into EditProposalRecord and verify against target source."""
        if not raw_payload or not raw_payload.strip():
            return None, ["Model output is empty or whitespace."]

        try:
            parsed_json = json.loads(raw_payload)
        except (ValueError, json.JSONDecodeError) as exc:
            return None, [f"Malformed JSON syntax: {exc}"]

        if not isinstance(parsed_json, dict):
            return None, [f"Expected JSON object (dict), got {type(parsed_json).__name__}."]

        try:
            record = EditProposalRecord.model_validate(parsed_json)
        except ValidationError as exc:
            error_msgs = [
                f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']} (got {err.get('input', 'missing')!r})"
                for err in exc.errors()
            ]
            return None, error_msgs

        result = self.edit_validator.validate_proposal(record)
        if not result.is_valid:
            return None, result.errors

        return record, []

    def validate_record(
        self,
        record: EditProposalRecord,
    ) -> tuple[bool, list[str]]:
        """Validate parsed EditProposalRecord against target source text."""
        result = self.edit_validator.validate_proposal(record)
        return result.is_valid, result.errors

