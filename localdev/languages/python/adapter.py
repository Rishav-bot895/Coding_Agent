"""Python language adapter implementation.

Implements the LanguageAdapter contract for Python targets, providing non-executing
syntax checks, AST fact extraction, execution preparation (-E -B -P), and validation skeletons.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from localdev.languages.base import LanguageAdapter
from localdev.schemas import (
    ASTClassFact,
    ASTFacts,
    ASTFunctionFact,
    ComplexityAbstentionReason,
    ComplexityClassEnum,
    ComplexityReport,
    ConfidenceEnum,
    DetectionResult,
    DiagnosticRecord,
    ExecutionResult,
    ExecutionSpec,
    LanguageCapabilities,
    SeverityEnum,
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
        source = self._read_source(target, source_text)
        try:
            ast.parse(source, filename=target.path, mode="exec")
            return []
        except (SyntaxError, IndentationError) as err:
            line = err.lineno or 1
            col = err.offset or 1
            end_line = max(err.end_lineno or line, line)
            end_col = err.end_offset or col
            if end_line == line:
                end_col = max(end_col, col)

            diag = DiagnosticRecord(
                source="python_syntax",
                code=type(err).__name__,
                message=err.msg or "Syntax error",
                severity=SeverityEnum.ERROR,
                start_line=line,
                start_col=col,
                end_line=end_line,
                end_col=end_col,
                fix_available=False,
            )
            return [diag]

    def extract_ast_facts(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> ASTFacts:
        source = self._read_source(target, source_text)
        total_lines = len(source.splitlines()) if source else 0
        try:
            tree = ast.parse(source, filename=target.path, mode="exec")
        except (SyntaxError, IndentationError) as err:
            return ASTFacts(
                functions=[],
                classes=[],
                total_lines=total_lines,
                syntax_error=str(err),
            )

        functions: list[ASTFunctionFact] = []
        classes: list[ASTClassFact] = []

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(
                    ASTFunctionFact(
                        name=node.name,
                        qualified_name=node.name,
                        start_line=node.lineno,
                        end_line=getattr(node, "end_lineno", node.lineno),
                        parameters=[arg.arg for arg in node.args.args],
                        is_async=isinstance(node, ast.AsyncFunctionDef),
                        is_method=False,
                        docstring=ast.get_docstring(node),
                    )
                )
            elif isinstance(node, ast.ClassDef):
                method_names: list[str] = []
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method_names.append(sub.name)
                        functions.append(
                            ASTFunctionFact(
                                name=sub.name,
                                qualified_name=f"{node.name}.{sub.name}",
                                start_line=sub.lineno,
                                end_line=getattr(sub, "end_lineno", sub.lineno),
                                parameters=[arg.arg for arg in sub.args.args],
                                is_async=isinstance(sub, ast.AsyncFunctionDef),
                                is_method=True,
                                docstring=ast.get_docstring(sub),
                            )
                        )
                classes.append(
                    ASTClassFact(
                        name=node.name,
                        start_line=node.lineno,
                        end_line=getattr(node, "end_lineno", node.lineno),
                        methods=method_names,
                    )
                )

        return ASTFacts(
            functions=functions,
            classes=classes,
            total_lines=total_lines,
            syntax_error=None,
        )

    def run_diagnostics(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> list[DiagnosticRecord]:
        # Baseline skeleton; integrated with isolated Ruff in P4-T3
        return []

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
