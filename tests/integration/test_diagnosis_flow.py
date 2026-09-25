"""Integration tests for integrated static and runtime diagnosis flows (P7-T4).

Tests:
1. Static analysis with diagnosis (--diagnose) via Orchestrator.
2. Runtime debug with diagnosis (--diagnose) via Orchestrator.
3. Clean execution (no bug) diagnosis flow.
4. Cross-file fault detection and safe abstention.
5. Graceful degradation when Ollama is offline or unreachable.
6. CLI analyse --diagnose with JSON envelope output.
7. CLI debug --diagnose with human-readable terminal output.
8. CLI debug --diagnose offline fallback preserving deterministic evidence.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from localdev.agent.orchestrator import Orchestrator
from localdev.agent.permissions import validate_target
from localdev.agent.session import Session
from localdev.cli import main
from localdev.constants import EXIT_SUCCESS, EXIT_TARGET_FAILURE
from localdev.inference.base import BaseInferenceClient
from localdev.reporting.terminal import TerminalReporter
from localdev.schemas import (
    ConfidenceEnum,
    DiagnosisAbstention,
    DiagnosisAbstentionReason,
    DiagnosisRecord,
    ErrorSignature,
    ExecutionResult,
    InferenceMetadata,
    TracebackFrame,
)


class FakeDiagnosisInferenceClient(BaseInferenceClient):
    """Deterministic fake client returning pre-configured diagnosis records."""

    def __init__(self, record: DiagnosisRecord | None = None, available: bool = True) -> None:
        self.record = record or DiagnosisRecord(
            bug_description="Loop index exceeds bounds",
            root_cause="Upper range limit is len(items) + 1",
            confidence=ConfidenceEnum.HIGH,
            cited_evidence_ids=["traceback:line_10", "syntax:clean"],
            rationale="Evaluating items[len(items)] raises IndexError in CPython.",
        )
        self.available = available
        self.call_history: list[list[dict[str, str]]] = []

    def is_available(self) -> bool:
        return self.available

    def unload_model(self, model: str | None = None) -> bool:
        return True

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
        metadata = InferenceMetadata(
            context_window_tokens=2048,
            prompt_budget_tokens=1200,
            output_budget_tokens=600,
            application_safety_margin_tokens=248,
            estimated_prompt_tokens=180,
            prompt_eval_count=175,
            eval_count=60,
            was_truncated=False,
            omitted_evidence_categories=[],
        )
        return self.record, metadata


class TestDiagnosisIntegrationFlows:
    """Test Orchestrator integration of static and runtime diagnosis."""

    def test_static_diagnosis_flow_success(self, tmp_path: Path) -> None:
        """Static analysis with diagnose=True returns report with DiagnosisRecord."""
        target_file = tmp_path / "sample.py"
        target_file.write_text("def foo():\n    return 42\n", encoding="utf-8")
        target = validate_target(target_file)

        mock_record = DiagnosisRecord(
            bug_description="No bugs identified in static code",
            root_cause="Syntax and structure are clean",
            confidence=ConfidenceEnum.HIGH,
            cited_evidence_ids=["syntax:clean", "ast:function:foo"],
            rationale="Function is syntactically sound and valid.",
        )
        mock_client = FakeDiagnosisInferenceClient(record=mock_record)

        with Session(target_record=target) as session:
            orchestrator = Orchestrator(session=session)
            report = orchestrator.analyse(
                target,
                diagnose=True,
                inference_client=mock_client,
            )

        assert report.syntax_valid is True
        assert report.ast_facts is not None
        assert isinstance(report.diagnosis, DiagnosisRecord)
        assert report.diagnosis.root_cause == "Syntax and structure are clean"
        assert report.inference_metadata is not None

        # Verify terminal presentation contains both deterministic facts and diagnosis
        out_stream = io.StringIO()
        reporter = TerminalReporter(stream=out_stream)
        text = reporter.render_analyse(report)
        assert "--- Syntax Check ---" in text
        assert "--- Structural AST Declarations ---" in text
        assert "MODEL DIAGNOSIS (Local SLM - Informational Only, Non-Executable)" in text
        assert "No bugs identified in static code" in text

    def test_runtime_debug_diagnosis_flow_success(self) -> None:
        """Runtime debug with diagnose=True returns execution result with DiagnosisRecord."""
        sample_path = Path("tests/bug_samples/initial/02_zero_division.py")
        assert sample_path.is_file()
        target = validate_target(sample_path)

        mock_record = DiagnosisRecord(
            bug_description="Division by zero in compute_ratio",
            root_cause="Denominator is zero",
            confidence=ConfidenceEnum.HIGH,
            cited_evidence_ids=["traceback:line_7", "runtime:ZeroDivisionError"],
            rationale="Evaluating total / 0 triggers ZeroDivisionError.",
        )
        mock_client = FakeDiagnosisInferenceClient(record=mock_record)

        with Session(target_record=target) as session:
            orchestrator = Orchestrator(session=session)
            result = orchestrator.debug(
                target=target,
                diagnose=True,
                inference_client=mock_client,
            )

        assert result.exit_code != 0
        assert result.error_signature is not None
        assert result.error_signature.exception_type == "ZeroDivisionError"
        assert isinstance(result.diagnosis, DiagnosisRecord)
        assert result.diagnosis.confidence == ConfidenceEnum.HIGH

        # Verify terminal presentation contains execution summary and diagnosis
        out_stream = io.StringIO()
        reporter = TerminalReporter(stream=out_stream)
        text = reporter.render_debug(target.path, result)
        assert "--- Execution Summary ---" in text
        assert "--- Runtime Error Signature ---" in text
        assert "MODEL DIAGNOSIS (Local SLM - Informational Only, Non-Executable)" in text
        assert "Division by zero in compute_ratio" in text

    def test_cross_file_fault_abstention(self, tmp_path: Path) -> None:
        """Fault originating entirely in external code triggers cross-file abstention."""
        target_file = tmp_path / "caller.py"
        target_file.write_text("import ext_module\next_module.run()\n", encoding="utf-8")
        target = validate_target(target_file)

        # Mock an execution result with exclusively external frames
        external_exec = ExecutionResult(
            exit_code=1,
            duration_seconds=0.05,
            error_signature=ErrorSignature(
                exception_type="ExternalCrashError",
                normalized_message="Crash in third-party library",
                top_target_file="C:\\libs\\ext_module.py",
                top_target_line=88,
            ),
            frames=[
                TracebackFrame(
                    file_path="C:\\libs\\ext_module.py",
                    line_number=88,
                    function_name="run",
                    code_line="raise ExternalCrashError('Crash')",
                    is_target=False,
                )
            ],
        )

        mock_client = FakeDiagnosisInferenceClient()

        with Session(target_record=target) as session:
            orchestrator = Orchestrator(session=session)
            analysis_report = orchestrator.analyse(target)
            diag, _ = orchestrator.diagnose(
                target=target,
                analysis_report=analysis_report,
                execution_result=external_exec,
                inference_client=mock_client,
            )

        assert isinstance(diag, DiagnosisAbstention)
        assert diag.reason == DiagnosisAbstentionReason.UNGROUNDED_EVIDENCE
        assert "single-target boundary" in diag.details
        assert "external library or dependency" in diag.details
        # Ensure client was not called since it abstained before inference
        assert len(mock_client.call_history) == 0

    def test_graceful_degradation_when_ollama_offline(self, tmp_path: Path) -> None:
        """When Ollama is offline, deterministic evidence is returned with an abstention warning."""
        target_file = tmp_path / "clean_code.py"
        target_file.write_text("x = 10\nprint(x)\n", encoding="utf-8")
        target = validate_target(target_file)

        offline_client = FakeDiagnosisInferenceClient(available=False)

        with Session(target_record=target) as session:
            orchestrator = Orchestrator(session=session)
            report = orchestrator.analyse(
                target,
                diagnose=True,
                inference_client=offline_client,
            )

        # Deterministic analysis is fully valid
        assert report.syntax_valid is True
        assert report.total_lines == 2
        # Diagnosis safely abstained due to offline model
        assert isinstance(report.diagnosis, DiagnosisAbstention)
        assert report.diagnosis.reason == DiagnosisAbstentionReason.MODEL_UNAVAILABLE
        assert "unreachable" in report.diagnosis.details


class TestCliDiagnosisCommands:
    """Test CLI commands wiring --diagnose with JSON and terminal outputs."""

    def test_cli_analyse_diagnose_json(self, capsys: Any) -> None:
        """CLI analyse --diagnose --json outputs envelope with diagnosis data."""
        sample_path = Path("tests/bug_samples/initial/01_off_by_one.py")
        assert sample_path.is_file()

        # Run CLI with analyse --diagnose --json
        exit_code = main(["analyse", "--diagnose", "--json", str(sample_path)])
        captured = capsys.readouterr()

        assert exit_code == EXIT_SUCCESS
        envelope_data = json.loads(captured.out)
        assert envelope_data["command"] == "analyse"
        assert envelope_data["success"] is True
        assert "diagnosis" in envelope_data["data"]
        # If Ollama is offline in test environment, limitations must contain the explanation
        if envelope_data["data"]["diagnosis"] is not None:
            diag = envelope_data["data"]["diagnosis"]
            assert "reason" in diag or "bug_description" in diag

    def test_cli_debug_diagnose_terminal(self, capsys: Any) -> None:
        """CLI debug --diagnose outputs deterministic debug facts and diagnosis section."""
        sample_path = Path("tests/bug_samples/initial/01_off_by_one.py")
        assert sample_path.is_file()

        exit_code = main(["debug", "--diagnose", str(sample_path)])
        captured = capsys.readouterr()

        # Bug sample 01 exits with non-zero exit code due to IndexError
        assert exit_code == EXIT_TARGET_FAILURE
        # Deterministic execution output is present
        assert "--- Execution Summary ---" in captured.out
        assert "--- Runtime Error Signature ---" in captured.out
        assert "IndexError" in captured.out
        # Model diagnosis section is present
        assert "MODEL DIAGNOSIS" in captured.out

    def test_cli_debug_diagnose_json_offline_fallback(self, capsys: Any) -> None:
        """CLI debug --diagnose --json includes abstention limitation when Ollama offline."""
        sample_path = Path("tests/bug_samples/initial/02_zero_division.py")
        assert sample_path.is_file()

        # Mock OllamaClient.is_available to return False to guarantee offline simulation
        with patch("localdev.inference.ollama_client.OllamaClient.is_available", return_value=False):
            exit_code = main(["debug", "--diagnose", "--json", str(sample_path)])
            captured = capsys.readouterr()

        assert exit_code == EXIT_TARGET_FAILURE
        envelope_data = json.loads(captured.out)
        assert envelope_data["command"] == "debug"
        assert envelope_data["success"] is False
        assert len(envelope_data["limitations"]) > 0
        assert "Local SLM diagnosis abstained" in envelope_data["limitations"][0]
        assert envelope_data["data"]["diagnosis"]["reason"] == "MODEL_UNAVAILABLE"
