"""Python language adapter implementation.

Implements the LanguageAdapter contract for Python targets, providing non-executing
syntax checks, AST fact extraction, execution preparation (-E -B -P), and validation skeletons.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from localdev.patching.edit_schema import EditOperation

from localdev.errors import LocaldevError
from localdev.languages.base import LanguageAdapter
from localdev.schemas import (
    ASTFacts,
    ComplexityAbstentionReason,
    ComplexityClassEnum,
    ComplexityReport,
    ConfidenceEnum,
    DetectionResult,
    DiagnosticRecord,
    ExecutionResult,
    ExecutionSpec,
    LanguageCapabilities,
    TargetRecord,
    ValidationLevel,
    ValidationReport,
)


class PythonAdapter(LanguageAdapter):
    """Language adapter for Python source targets."""

    @property
    def name(self) -> str:
        return "python"

    @property
    def file_extensions(self) -> tuple[str, ...]:
        return (".py", ".pyw")

    @property
    def capabilities(self) -> LanguageCapabilities:
        return LanguageCapabilities(
            supports_syntax_check=True,
            supports_ast_facts=True,
            supports_diagnostics=True,
            supports_execution=True,
            supports_complexity=True,
            supports_validation=True,
        )

    def _read_source(self, target: TargetRecord, source_text: str | None = None) -> str:
        if source_text is not None:
            return source_text
        target_path = Path(target.absolute_path)
        if not target_path.is_file():
            return ""
        try:
            return target_path.read_text(encoding=target.encoding)
        except (OSError, UnicodeDecodeError):
            try:
                return target_path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                return ""

    def detect_confidence(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> DetectionResult:
        from localdev.languages.detector import detect_target_language

        _, result = detect_target_language(target, source_text=source_text)
        return result

    def check_syntax(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> list[DiagnosticRecord]:
        from localdev.languages.python.syntax import validate_python_syntax

        return validate_python_syntax(target, source_text=source_text)

    def extract_ast_facts(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> ASTFacts:
        from localdev.languages.python.ast_analyser import extract_ast_facts

        return extract_ast_facts(target, source_text=source_text)

    def run_diagnostics(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> list[DiagnosticRecord]:
        from localdev.languages.python.diagnostics import run_target_diagnostics

        return run_target_diagnostics(target, source_text=source_text)

    def prepare_execution(
        self,
        target: TargetRecord,
        args: list[str] | None = None,
    ) -> ExecutionSpec:
        command_args = [
            sys.executable,
            "-E",
            "-B",
            "-P",
            target.absolute_path,
            *(args or []),
        ]
        return ExecutionSpec(
            command_args=command_args,
            env_overrides={},
            cwd=str(Path.cwd().resolve()),
        )

    def analyze_complexity(
        self,
        target: TargetRecord,
        source_text: str | None = None,
        selector: str | None = None,
    ) -> ComplexityReport:
        target_name = target.path if not selector else f"{target.path}::{selector}"
        return ComplexityReport(
            target=target_name,
            time_complexity=ComplexityClassEnum.UNKNOWN,
            auxiliary_space=ComplexityClassEnum.UNKNOWN,
            output_space=ComplexityClassEnum.UNKNOWN,
            confidence=ConfidenceEnum.LOW,
            is_amortized=False,
            is_expected=False,
            assumptions=[],
            abstention_reason=ComplexityAbstentionReason.UNSUPPORTED_SYNTAX,
            details="Complexity analysis skeleton.",
        )

    def validate_candidate(
        self,
        target: TargetRecord,
        candidate_path: Path | str,
        baseline_result: ExecutionResult | None = None,
        expected_stdout: str | None = None,
        expected_stdout_contains: str | None = None,
        expected_exit: int | None = None,
        baseline_diagnostics: Sequence[DiagnosticRecord] | None = None,
        edits: Sequence[EditOperation] | None = None,
        targeted_diagnostics: Sequence[DiagnosticRecord | str] | None = None,
        baseline_syntax_valid: bool = True,
    ) -> ValidationReport:
        cand_path = Path(candidate_path).resolve()
        try:
            cand_text = cand_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ValidationReport(
                level_achieved=ValidationLevel.NONE,
                static_valid=False,
                failure_reproduction_removed=False,
                clean_execution=False,
                behavioral_oracle_passed=None,
                details={"error": f"Failed to read candidate: {exc}"},
            )

        # Level A: Static validity (syntax check + isolated Ruff baseline diff)
        from localdev.languages.python.diagnostics import evaluate_level_a

        base_diags = (
            baseline_diagnostics
            if baseline_diagnostics is not None
            else self.run_diagnostics(target)
        )

        level_a = evaluate_level_a(
            candidate_source=cand_text,
            baseline_diagnostics=base_diags,
            edits=edits,
            targeted_diagnostics=targeted_diagnostics,
            baseline_syntax_valid=baseline_syntax_valid,
        )

        if not level_a.passed:
            return ValidationReport(
                level_achieved=ValidationLevel.NONE,
                static_valid=False,
                failure_reproduction_removed=False,
                clean_execution=False,
                behavioral_oracle_passed=None,
                details={
                    "level_a": level_a.model_dump(),
                    "failure_reasons": level_a.failure_reasons,
                },
            )

        static_valid = True
        level_achieved = ValidationLevel.LEVEL_A
        failure_removed = False
        clean_exec = False
        oracle_passed: bool | None = None
        details: dict[str, object] = {
            "level_a": level_a.model_dump(),
        }

        # Levels B, C, D: Subprocess execution of candidate
        if cand_path.is_file():
            from localdev.agent.evidence import _execute_and_parse

            try:
                candidate_exec = _execute_and_parse(
                    target=target,
                    session_target=cand_path,
                )
                details["candidate_exit_code"] = candidate_exec.exit_code
                details["candidate_stdout"] = candidate_exec.stdout
                details["candidate_stderr"] = candidate_exec.stderr

                # Level B: Failure reproduction removed
                if baseline_result is not None:
                    if baseline_result.error_signature is not None:
                        orig_sig = baseline_result.error_signature
                        cand_sig = candidate_exec.error_signature
                        if cand_sig is None or (
                            cand_sig.exception_type != orig_sig.exception_type
                            or cand_sig.normalized_message != orig_sig.normalized_message
                        ):
                            failure_removed = True
                            level_achieved = ValidationLevel.LEVEL_B
                    elif baseline_result.exit_code != 0:
                        if candidate_exec.exit_code == 0 or candidate_exec.exit_code != baseline_result.exit_code:
                            failure_removed = True
                            level_achieved = ValidationLevel.LEVEL_B
                else:
                    failure_removed = True

                # Level C: Clean execution (exit code 0 under controlled limits)
                if candidate_exec.exit_code == 0 and not candidate_exec.timed_out:
                    clean_exec = True
                    level_achieved = ValidationLevel.LEVEL_C

                # Level D: Behavioral oracle
                has_oracle = (
                    expected_stdout is not None
                    or expected_stdout_contains is not None
                    or expected_exit is not None
                )
                if has_oracle:
                    oracle_ok = True
                    if expected_exit is not None and candidate_exec.exit_code != expected_exit:
                        oracle_ok = False
                    if expected_stdout is not None and candidate_exec.stdout.strip() != expected_stdout.strip():
                        oracle_ok = False
                    if expected_stdout_contains is not None and expected_stdout_contains not in candidate_exec.stdout:
                        oracle_ok = False

                    oracle_passed = oracle_ok
                    if oracle_ok and clean_exec:
                        level_achieved = ValidationLevel.LEVEL_D
            except (LocaldevError, OSError, RuntimeError) as exc:
                details["execution_error"] = str(exc)

        return ValidationReport(
            level_achieved=level_achieved,
            static_valid=static_valid,
            failure_reproduction_removed=failure_removed,
            clean_execution=clean_exec,
            behavioral_oracle_passed=oracle_passed,
            details=details,
        )
