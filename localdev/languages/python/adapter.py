"""Python language adapter implementation.

Implements the LanguageAdapter contract for Python targets, providing non-executing
syntax checks, AST fact extraction, execution preparation (-E -B -P), and validation skeletons.
"""

from __future__ import annotations

import sys
from pathlib import Path

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
            cwd=str(Path(target.absolute_path).parent),
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
    ) -> ValidationReport:
        return ValidationReport(
            level_achieved=ValidationLevel.NONE,
            static_valid=False,
            failure_reproduction_removed=False,
            clean_execution=False,
            behavioral_oracle_passed=None,
            details={"status": "validation_skeleton"},
        )
