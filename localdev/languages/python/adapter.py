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
    ComplexityReport,
    DetectionResult,
    DiagnosticRecord,
    ErrorSignature,
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
        from localdev.languages.python.complexity import analyze_complexity as _analyze

        return _analyze(target, source_text=source_text, selector=selector)

    def analyze_file_complexity(
        self,
        target: TargetRecord,
        source_text: str | None = None,
    ) -> list[ComplexityReport]:
        from localdev.languages.python.complexity import (
            analyze_file_complexity as _analyze_file,
        )

        return _analyze_file(target, source_text=source_text)

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
        target_args: Sequence[str] | None = None,
        stdin_file: str | Path | None = None,
        timeout: float | None = None,
        fail_on_job_failure: bool = False,
    ) -> ValidationReport:
        cand_path = Path(candidate_path).resolve()
        has_oracle = (
            expected_stdout is not None
            or expected_stdout_contains is not None
            or expected_exit is not None
        )
        try:
            cand_text = cand_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ValidationReport(
                level_achieved=ValidationLevel.NONE,
                static_valid=False,
                failure_reproduction_removed=False,
                clean_execution=False,
                behavioral_oracle_passed=False if has_oracle else None,
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
                behavioral_oracle_passed=False if has_oracle else None,
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
        cand_sig: ErrorSignature | None = None
        orig_sig: ErrorSignature | None = (
            baseline_result.error_signature if baseline_result else None
        )

        if cand_path.is_file():
            from localdev.agent.evidence import _execute_and_parse
            from localdev.languages.python.traceback_parser import (
                is_same_error_signature,
            )

            try:
                candidate_exec = _execute_and_parse(
                    target=target,
                    session_target=cand_path,
                    target_args=target_args,
                    stdin_file=stdin_file,
                    timeout=timeout,
                    fail_on_job_failure=fail_on_job_failure,
                )
                cand_sig = candidate_exec.error_signature

                details["candidate_exit_code"] = candidate_exec.exit_code
                details["candidate_stdout"] = candidate_exec.stdout
                details["candidate_stderr"] = candidate_exec.stderr
                details["candidate_timed_out"] = candidate_exec.timed_out
                if cand_sig:
                    details["candidate_error_signature"] = cand_sig.model_dump()
                if orig_sig:
                    details["baseline_error_signature"] = orig_sig.model_dump()

                # Timeout regression: candidate fails Level B & C
                if candidate_exec.timed_out:
                    failure_removed = False
                    clean_exec = False
                    details["runtime_status"] = (
                        "Candidate timed out under controlled execution limits (regression detected)."
                    )
                elif baseline_result is not None:
                    if orig_sig is not None:
                        if candidate_exec.exit_code == 0 and not candidate_exec.timed_out:
                            # Clean execution: original exception removed, exit code 0
                            failure_removed = True
                            clean_exec = True
                            level_achieved = ValidationLevel.LEVEL_C
                            details["runtime_status"] = (
                                "Candidate exited cleanly with code 0. Original failure reproduction removed."
                            )
                        else:
                            # Non-zero exit: compare error signatures
                            is_reproduced = is_same_error_signature(orig_sig, cand_sig, edits=edits)
                            if is_reproduced:
                                failure_removed = False
                                clean_exec = False
                                details["runtime_status"] = (
                                    f"Original runtime failure was reproduced: {orig_sig.exception_type}: {orig_sig.normalized_message}."
                                )
                            else:
                                # Failure reproduction removed! Replaced by different error / non-zero exit
                                failure_removed = True
                                clean_exec = False
                                level_achieved = ValidationLevel.LEVEL_B
                                different_err = (
                                    cand_sig.exception_type
                                    if cand_sig
                                    else f"exit code {candidate_exec.exit_code}"
                                )
                                details["runtime_status"] = (
                                    f"Original failure ({orig_sig.exception_type}) removed, but candidate failed "
                                    f"with different error ({different_err}). Level B does NOT prove the bug is fixed."
                                )
                    elif baseline_result.exit_code != 0:
                        # Baseline had non-zero exit code without parsed exception
                        if candidate_exec.exit_code == 0 and not candidate_exec.timed_out:
                            failure_removed = True
                            clean_exec = True
                            level_achieved = ValidationLevel.LEVEL_C
                            details["runtime_status"] = "Candidate exited cleanly with code 0."
                        elif candidate_exec.exit_code != baseline_result.exit_code:
                            failure_removed = True
                            clean_exec = False
                            level_achieved = ValidationLevel.LEVEL_B
                            details["runtime_status"] = (
                                f"Original exit code {baseline_result.exit_code} changed to {candidate_exec.exit_code}. "
                                "Level B does NOT prove the bug is fixed."
                            )
                        else:
                            failure_removed = False
                            clean_exec = False
                            details["runtime_status"] = (
                                f"Original exit code {baseline_result.exit_code} still reproduced."
                            )
                    else:
                        # Baseline was clean (exit code 0)
                        if candidate_exec.exit_code == 0 and not candidate_exec.timed_out:
                            failure_removed = True
                            clean_exec = True
                            level_achieved = ValidationLevel.LEVEL_C
                            details["runtime_status"] = "Candidate executed cleanly with exit code 0."
                        else:
                            # Candidate introduced a crash on clean baseline
                            failure_removed = False
                            clean_exec = False
                            err_desc = (
                                cand_sig.exception_type
                                if cand_sig
                                else f"exit code {candidate_exec.exit_code}"
                            )
                            details["runtime_status"] = (
                                f"Candidate crashed ({err_desc}) on previously clean baseline (regression detected)."
                            )
                else:
                    # No baseline provided
                    if candidate_exec.exit_code == 0 and not candidate_exec.timed_out:
                        failure_removed = True
                        clean_exec = True
                        level_achieved = ValidationLevel.LEVEL_C
                        details["runtime_status"] = "Candidate executed cleanly with exit code 0."
                    else:
                        failure_removed = False
                        clean_exec = False
                        details["runtime_status"] = (
                            f"Candidate exited with non-zero code {candidate_exec.exit_code}."
                        )

                # Level D: Behavioral oracle
                if has_oracle:
                    oracle_ok = True
                    details["oracle"] = {
                        "expected_stdout": expected_stdout,
                        "expected_stdout_contains": expected_stdout_contains,
                        "expected_exit": expected_exit,
                    }

                    # Check exit code
                    if expected_exit is not None:
                        if candidate_exec.exit_code != expected_exit:
                            oracle_ok = False
                            details["oracle_failure_reason"] = (
                                f"Expected exit code {expected_exit}, but candidate exited with {candidate_exec.exit_code}."
                            )
                    elif candidate_exec.exit_code != 0:
                        oracle_ok = False
                        details["oracle_failure_reason"] = (
                            f"Candidate exited with non-zero exit code {candidate_exec.exit_code}."
                        )

                    # Check timeout
                    if candidate_exec.timed_out:
                        oracle_ok = False
                        details["oracle_failure_reason"] = "Candidate execution timed out."

                    # Check exact stdout
                    if expected_stdout is not None and oracle_ok:
                        actual_stdout = candidate_exec.stdout
                        norm_act = actual_stdout.replace("\r\n", "\n")
                        norm_exp = expected_stdout.replace("\r\n", "\n")
                        if (
                            actual_stdout != expected_stdout
                            and norm_act != norm_exp
                            and actual_stdout.strip() != expected_stdout.strip()
                        ):
                            oracle_ok = False
                            details["oracle_failure_reason"] = (
                                f"Candidate stdout did not match expected stdout.\n"
                                f"Expected: {expected_stdout!r}\nActual:   {actual_stdout!r}"
                            )

                    # Check substring stdout
                    if expected_stdout_contains is not None and oracle_ok:
                        actual_stdout = candidate_exec.stdout
                        norm_act = actual_stdout.replace("\r\n", "\n")
                        norm_sub = expected_stdout_contains.replace("\r\n", "\n")
                        if (
                            expected_stdout_contains not in actual_stdout
                            and norm_sub not in norm_act
                        ):
                            oracle_ok = False
                            details["oracle_failure_reason"] = (
                                f"Candidate stdout did not contain expected substring {expected_stdout_contains!r}."
                            )

                    # Strict Level D certification contract:
                    # Level D is awarded IF AND ONLY IF Level C is achieved AND oracle is satisfied.
                    if oracle_ok and clean_exec:
                        oracle_passed = True
                        level_achieved = ValidationLevel.LEVEL_D
                        details["oracle_status"] = "All behavioral assertions satisfied (Level D certified)."
                    else:
                        oracle_passed = False
                        if "oracle_failure_reason" not in details and not clean_exec:
                            details["oracle_failure_reason"] = (
                                "Level C (clean execution with exit code 0) required for Level D certification."
                            )
                else:
                    oracle_passed = None
            except (LocaldevError, OSError, RuntimeError) as exc:
                details["execution_error"] = str(exc)
                if has_oracle:
                    oracle_passed = False

        if has_oracle and oracle_passed is None:
            oracle_passed = False

        return ValidationReport(
            level_achieved=level_achieved,
            static_valid=static_valid,
            failure_reproduction_removed=failure_removed,
            clean_execution=clean_exec,
            behavioral_oracle_passed=oracle_passed,
            baseline_error_signature=orig_sig,
            candidate_error_signature=cand_sig,
            details=details,
        )
