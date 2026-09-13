"""Unit tests for strongly typed Pydantic schemas, validation rules, and error hierarchy.

Verifies:
1. Round-trip JSON serialization and deserialization for all schemas.
2. Loading of valid and invalid schema fixtures.
3. Rejection of negative numbers, reversed line ranges, reversed columns, and invalid enums.
4. Enforcement of patch limits: max 8 edits, max 80 changed lines, shell path safety.
5. Simple, compliant JSON Schema generation via .model_json_schema() for Ollama.
6. Mapping of error hierarchy exceptions to defined stable CLI exit codes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from localdev.constants import (
    EXIT_ABSTENTION,
    EXIT_CLI_USAGE_ERROR,
    EXIT_INFERENCE_ERROR,
    EXIT_TARGET_FAILURE,
    EXIT_TARGET_IO_ERROR,
    EXIT_TIMEOUT_RESOURCE_BREACH,
)
from localdev.errors import (
    AbstentionError,
    CliUsageError,
    ExecutionTimeoutError,
    InferenceError,
    LocaldevError,
    OutputCapExceededError,
    PatchApplicationError,
    ResourceBreachError,
    SchemaValidationError,
    StaleEditError,
    TargetValidationError,
)
from localdev.schemas import (
    ASTClassFact,
    ASTFacts,
    ASTFunctionFact,
    ComplexityAbstentionReason,
    ComplexityClassEnum,
    ComplexityReport,
    ConfidenceEnum,
    DiagnosisRecord,
    DiagnosticRecord,
    EditOperation,
    EditOperationType,
    EditProposalRecord,
    ErrorSignature,
    ExecutionResult,
    InferenceMetadata,
    JsonEnvelope,
    ProfileReport,
    SeverityEnum,
    TargetRecord,
    TracebackFrame,
    ValidationLevel,
    ValidationReport,
)

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "schema"


# =============================================================================
# Round-trip and Fixture Tests
# =============================================================================


def test_target_record_roundtrip() -> None:
    """Verify TargetRecord serialization and immutability."""
    record = TargetRecord(
        path="script.py",
        absolute_path="C:\\workspace\\script.py",
        file_size_bytes=1024,
        sha256="a" * 64,
        encoding="utf-8",
        has_bom=False,
        newline_style="\r\n",
        has_trailing_newline=True,
        is_read_only=False,
        is_reparse_point=False,
    )
    raw = record.model_dump_json()
    loaded = TargetRecord.model_validate_json(raw)
    assert loaded == record

    with pytest.raises(ValidationError):
        # TargetRecord is frozen
        record.path = "new_path.py"  # type: ignore[misc]


def test_diagnostic_record_roundtrip() -> None:
    """Verify DiagnosticRecord roundtrip and validation."""
    diag = DiagnosticRecord(
        source="ruff",
        code="E501",
        message="Line too long (120 > 100)",
        severity=SeverityEnum.WARNING,
        start_line=10,
        start_col=1,
        end_line=10,
        end_col=120,
        fix_available=True,
    )
    raw = diag.model_dump_json()
    loaded = DiagnosticRecord.model_validate_json(raw)
    assert loaded == diag


def test_execution_result_roundtrip() -> None:
    """Verify ExecutionResult roundtrip with traceback frames and signature."""
    frame = TracebackFrame(
        file_path="C:\\project\\app.py",
        line_number=25,
        function_name="main",
        code_line="divide(1, 0)",
        is_target=True,
    )
    sig = ErrorSignature(
        exception_type="ZeroDivisionError",
        normalized_message="division by zero",
        top_target_file="C:\\project\\app.py",
        top_target_line=25,
    )
    res = ExecutionResult(
        exit_code=1,
        stdout="Starting calculation...\n",
        stderr="ZeroDivisionError: division by zero\n",
        duration_seconds=0.15,
        timed_out=False,
        output_truncated=False,
        peak_process_tree_rss_bytes=12451840,
        execution_backend="windows_job",
        error_signature=sig,
        frames=[frame],
    )
    raw = res.model_dump_json()
    loaded = ExecutionResult.model_validate_json(raw)
    assert loaded == res


def test_ast_facts_roundtrip() -> None:
    """Verify ASTFacts serialization with functions and classes."""
    fn = ASTFunctionFact(
        name="compute",
        qualified_name="Worker.compute",
        start_line=12,
        end_line=24,
        parameters=["self", "data", "factor"],
        is_async=False,
        is_method=True,
        docstring="Perform computation.",
    )
    cls_fact = ASTClassFact(
        name="Worker",
        start_line=10,
        end_line=30,
        methods=["compute"],
    )
    facts = ASTFacts(
        functions=[fn],
        classes=[cls_fact],
        total_lines=45,
        syntax_error=None,
    )
    raw = facts.model_dump_json()
    loaded = ASTFacts.model_validate_json(raw)
    assert loaded == facts


def test_validation_report_roundtrip() -> None:
    """Verify ValidationReport serialization across Levels A-D."""
    report = ValidationReport(
        level_achieved=ValidationLevel.LEVEL_C,
        static_valid=True,
        failure_reproduction_removed=True,
        clean_execution=True,
        behavioral_oracle_passed=None,
        details={"syntax": "pass", "ruff_diagnostics_remaining": 0},
    )
    raw = report.model_dump_json()
    loaded = ValidationReport.model_validate_json(raw)
    assert loaded == report


def test_inference_metadata_roundtrip() -> None:
    """Verify InferenceMetadata budget partition constraint and serialization."""
    meta = InferenceMetadata(
        context_window_tokens=2048,
        prompt_budget_tokens=1200,
        output_budget_tokens=600,
        application_safety_margin_tokens=248,
        estimated_prompt_tokens=850,
        prompt_eval_count=872,
        eval_count=145,
        prompt_eval_duration_ms=450.2,
        eval_duration_ms=890.5,
        was_truncated=False,
        omitted_evidence_categories=[],
    )
    raw = meta.model_dump_json()
    loaded = InferenceMetadata.model_validate_json(raw)
    assert loaded == meta


def test_json_envelope_roundtrip() -> None:
    """Verify generic JsonEnvelope serialization with typed payload."""
    payload = {"count": 42, "status": "active"}
    envelope = JsonEnvelope[dict[str, Any]](
        schema_version="1.0",
        command="info",
        success=True,
        target_path="target.py",
        data=payload,
        errors=[],
        limitations=[],
        metadata={"platform": "win32"},
    )
    raw = envelope.model_dump_json()
    loaded = JsonEnvelope[dict[str, Any]].model_validate_json(raw)
    assert loaded == envelope
    assert loaded.schema_version == "1.0"


def test_complexity_abstention_reasons() -> None:
    """Verify all defined complexity abstention reason codes."""
    assert set(ComplexityAbstentionReason) == {
        ComplexityAbstentionReason.UNKNOWN_CALL,
        ComplexityAbstentionReason.DYNAMIC_BOUNDS,
        ComplexityAbstentionReason.DYNAMIC_RECURSION,
        ComplexityAbstentionReason.EXTERNAL_DEPENDENCY,
        ComplexityAbstentionReason.UNSUPPORTED_SYNTAX,
    }


# =============================================================================
# Fixture Validation Tests
# =============================================================================


def test_valid_fixtures() -> None:
    """Verify all valid schema JSON fixtures parse successfully."""
    # Diagnosis fixture
    diag_file = FIXTURES_DIR / "valid_diagnosis.json"
    diag = DiagnosisRecord.model_validate_json(diag_file.read_text(encoding="utf-8"))
    assert diag.confidence == ConfidenceEnum.HIGH
    assert len(diag.cited_evidence_ids) == 3

    # Edit proposal fixture
    edit_file = FIXTURES_DIR / "valid_edit_proposal.json"
    proposal = EditProposalRecord.model_validate_json(edit_file.read_text(encoding="utf-8"))
    assert proposal.target_file == "target_script.py"
    assert len(proposal.edits) == 1
    assert proposal.edits[0].operation == EditOperationType.REPLACE

    # Complexity fixture
    comp_file = FIXTURES_DIR / "valid_complexity.json"
    comp = ComplexityReport.model_validate_json(comp_file.read_text(encoding="utf-8"))
    assert comp.time_complexity == ComplexityClassEnum.O_N_LOG_N
    assert comp.auxiliary_space == ComplexityClassEnum.O_N

    # Profile fixture
    prof_file = FIXTURES_DIR / "valid_profile.json"
    prof = ProfileReport.model_validate_json(prof_file.read_text(encoding="utf-8"))
    assert prof.warmup_invocations == 3
    assert prof.measured_invocations == 10
    assert prof.python_allocations_tracemalloc_bytes == 45056

    # Envelope fixture
    env_file = FIXTURES_DIR / "valid_envelope.json"
    env = JsonEnvelope[dict[str, Any]].model_validate_json(env_file.read_text(encoding="utf-8"))
    assert env.schema_version == "1.0"
    assert env.command == "analyse"


def test_invalid_fixtures() -> None:
    """Verify invalid fixtures trigger ValidationError on parse."""
    rev_file = FIXTURES_DIR / "invalid_reversed_lines_edit.json"
    with pytest.raises(ValidationError) as exc_rev:
        EditProposalRecord.model_validate_json(rev_file.read_text(encoding="utf-8"))
    assert "start_line (20) <= end_line (10)" in str(exc_rev.value)

    unk_file = FIXTURES_DIR / "invalid_unknown_complexity.json"
    with pytest.raises(ValidationError) as exc_unk:
        ComplexityReport.model_validate_json(unk_file.read_text(encoding="utf-8"))
    assert "time_complexity" in str(exc_unk.value)


# =============================================================================
# Validation Rejection Edge Cases
# =============================================================================


def test_diagnostic_reversed_range_rejection() -> None:
    """Assert rejection of reversed line or column coordinates in DiagnosticRecord."""
    with pytest.raises(ValidationError) as exc_info:
        DiagnosticRecord(
            source="ruff",
            code="E999",
            message="Error",
            severity=SeverityEnum.ERROR,
            start_line=15,
            start_col=1,
            end_line=10,
            end_col=5,
        )
    assert "start_line (15) cannot be greater than end_line (10)" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_col:
        DiagnosticRecord(
            source="ruff",
            code="E999",
            message="Error",
            severity=SeverityEnum.ERROR,
            start_line=10,
            start_col=20,
            end_line=10,
            end_col=5,
        )
    assert "start_col (20) cannot be greater than end_col (5)" in str(exc_col.value)


def test_edit_operation_rules() -> None:
    """Assert line validation rules across replace, insert, and delete operations."""
    # Negative line numbers
    with pytest.raises(ValidationError):
        EditOperation(
            operation=EditOperationType.REPLACE,
            start_line=0,
            end_line=1,
            expected_text="a",
            replacement_text="b",
        )

    # Insert with unequal start and end lines
    with pytest.raises(ValidationError) as exc_insert:
        EditOperation(
            operation=EditOperationType.INSERT,
            start_line=5,
            end_line=6,
            expected_text="",
            replacement_text="new_code()",
        )
    assert "insert operation requires start_line == end_line" in str(exc_insert.value)


def test_edit_proposal_limits_rejection() -> None:
    """Assert rejection when edit count exceeds 8 or changed lines exceed 80."""
    # Exceeding 8 edits
    too_many_edits = [
        EditOperation(
            operation=EditOperationType.REPLACE,
            start_line=i + 1,
            end_line=i + 1,
            expected_text="old",
            replacement_text="new",
        )
        for i in range(9)
    ]
    with pytest.raises(ValidationError):
        EditProposalRecord(
            target_file="app.py",
            edits=too_many_edits,
            explanation="Too many edits",
        )

    # Exceeding 80 changed lines
    massive_edit = EditOperation(
        operation=EditOperationType.REPLACE,
        start_line=1,
        end_line=85,
        expected_text="x\n" * 85,
        replacement_text="y\n" * 85,
    )
    with pytest.raises(ValidationError) as exc_lines:
        EditProposalRecord(
            target_file="app.py",
            edits=[massive_edit],
            explanation="Too many lines changed",
        )
    assert "exceeds hard limit of 80" in str(exc_lines.value)


def test_edit_proposal_path_safety() -> None:
    """Assert rejection of path traversal and shell metacharacters in target_file."""
    unsafe_paths = [
        "../secret.py",
        "..\\secret.py",
        "app.py; rm -rf",
        "target.py | dir",
        "target.py & calc.exe",
        "target.py > output.txt",
        "`whoami`.py",
        "$HOME/script.py",
    ]
    for path in unsafe_paths:
        with pytest.raises(ValidationError):
            EditProposalRecord(
                target_file=path,
                edits=[
                    EditOperation(
                        operation=EditOperationType.REPLACE,
                        start_line=1,
                        end_line=1,
                        expected_text="a",
                        replacement_text="b",
                    )
                ],
                explanation="Testing path safety",
            )


def test_inference_metadata_budget_partition_check() -> None:
    """Assert rejection when prompt + output + margin does not equal context window."""
    with pytest.raises(ValidationError) as exc_info:
        InferenceMetadata(
            context_window_tokens=2048,
            prompt_budget_tokens=1500,  # 1500 + 600 + 248 = 2348 != 2048
            output_budget_tokens=600,
            application_safety_margin_tokens=248,
            estimated_prompt_tokens=500,
        )
    assert "Budget partition must sum to context window" in str(exc_info.value)


def test_envelope_unknown_schema_version() -> None:
    """Assert rejection of unsupported schema versions."""
    with pytest.raises(ValidationError):
        JsonEnvelope[None](
            schema_version="2.0",  # type: ignore[arg-type]
            command="info",
            success=True,
        )


def test_extra_forbidden_fields() -> None:
    """Assert schemas strictly forbid unexpected extra fields."""
    with pytest.raises(ValidationError):
        DiagnosisRecord.model_validate_json(
            json.dumps(
                {
                    "bug_description": "bug",
                    "root_cause": "cause",
                    "confidence": "HIGH",
                    "cited_evidence_ids": [],
                    "rationale": "rationale",
                    "unauthorized_shell_command": "rmdir /s /q C:\\",
                }
            )
        )


# =============================================================================
# Ollama Structured Output JSON Schema Export Tests
# =============================================================================


def test_diagnosis_record_model_json_schema() -> None:
    """Assert DiagnosisRecord.model_json_schema() produces compliant, simple JSON Schema."""
    schema = DiagnosisRecord.model_json_schema()
    assert schema["type"] == "object"
    assert "properties" in schema
    assert "required" in schema

    props = schema["properties"]
    assert "bug_description" in props
    assert "root_cause" in props
    assert "confidence" in props
    assert "cited_evidence_ids" in props
    assert "rationale" in props

    # Verify enum definition for confidence
    conf_prop = props["confidence"]
    # In Pydantic v2, enums may be defined directly or via $defs
    if "$ref" in conf_prop:
        def_key = conf_prop["$ref"].split("/")[-1]
        enum_def = schema["$defs"][def_key]
        assert set(enum_def["enum"]) == {"HIGH", "MEDIUM", "LOW"}
    else:
        assert set(conf_prop["enum"]) == {"HIGH", "MEDIUM", "LOW"}

    # Ensure no overly complex keywords are emitted
    assert "anyOf" not in props["bug_description"]
    assert "oneOf" not in props["bug_description"]


def test_edit_proposal_model_json_schema() -> None:
    """Assert EditProposalRecord.model_json_schema() produces clean structured schema."""
    schema = EditProposalRecord.model_json_schema()
    assert schema["type"] == "object"
    assert "properties" in schema
    assert "target_file" in schema["properties"]
    assert "edits" in schema["properties"]
    assert "explanation" in schema["properties"]


# =============================================================================
# Error Hierarchy Exit Code Tests
# =============================================================================


def test_error_hierarchy_exit_codes() -> None:
    """Verify all exceptions map to their defined CLI exit codes."""
    cases = [
        (LocaldevError("generic error"), EXIT_TARGET_FAILURE),
        (TargetValidationError("file missing"), EXIT_TARGET_IO_ERROR),
        (StaleEditError("stale hash", "exp", "act"), EXIT_TARGET_IO_ERROR),
        (CliUsageError("invalid flag"), EXIT_CLI_USAGE_ERROR),
        (ResourceBreachError("breached", "timeout"), EXIT_TIMEOUT_RESOURCE_BREACH),
        (ExecutionTimeoutError("timed out", 10.0), EXIT_TIMEOUT_RESOURCE_BREACH),
        (OutputCapExceededError("capped", 512), EXIT_TIMEOUT_RESOURCE_BREACH),
        (InferenceError("ollama down"), EXIT_INFERENCE_ERROR),
        (SchemaValidationError("invalid json shape"), EXIT_INFERENCE_ERROR),
        (AbstentionError("unknown call", "UNKNOWN_CALL"), EXIT_ABSTENTION),
        (PatchApplicationError("patch failed"), EXIT_TARGET_FAILURE),
    ]
    for exc, expected_code in cases:
        assert exc.exit_code == expected_code, f"{exc.__class__.__name__} exit_code != {expected_code}"
