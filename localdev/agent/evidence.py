"""Evidence collection and deterministic static/runtime analysis workflows.

Coordinates deterministic static and runtime evidence collection:
1. Static analysis: syntax validation, short-circuiting, AST extraction, isolated Ruff.
2. Runtime debug execution: controlled subprocess execution (-E -B -P), session
   relocation, asynchronous pipe draining, timeout enforcement, traceback parsing,
   frame classification (target vs external), and error signature extraction.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from localdev.agent.session import Session
from localdev.execution.limits import ExecutionLimits
from localdev.execution.runner import build_execution_request, run_execution_request
from localdev.languages.python.traceback_parser import parse_traceback
from localdev.schemas import (
    AnalysisReport,
    ErrorSignature,
    ExecutionResult,
    SeverityEnum,
)

if TYPE_CHECKING:
    from localdev.agent.orchestrator import Orchestrator
    from localdev.schemas import TargetRecord


def _read_target_text(target: TargetRecord, source_text: str | None = None) -> str:
    """Read source text from override or target file with encoding fallback."""
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


def collect_analysis_evidence(
    orchestrator: Orchestrator,
    target: TargetRecord,
    source_text: str | None = None,
) -> AnalysisReport:
    """Run the deterministic analyse workflow for a target file.

    Workflow:
    1. Check syntax without executing code or emitting bytecode.
    2. Short-circuit: if syntax errors exist, skip Ruff and AST extraction.
    3. If valid, extract bounded AST facts and run isolated Ruff diagnostics.

    Args:
        orchestrator: Active agent orchestrator.
        target: Validated target file metadata.
        source_text: Optional direct source code text override.

    Returns:
        AnalysisReport containing syntax status, AST facts, and normalized diagnostics.
    """
    source = _read_target_text(target, source_text=source_text)
    total_lines = len(source.splitlines()) if source else 0

    # Step 1: Syntax check
    syntax_diags = orchestrator.check_syntax(target, source_text=source_text)
    has_syntax_errors = any(d.severity == SeverityEnum.ERROR for d in syntax_diags)

    # Step 2: Short-circuit on syntax errors
    if has_syntax_errors:
        return AnalysisReport(
            target=target,
            total_lines=total_lines,
            syntax_valid=False,
            syntax_diagnostics=syntax_diags,
            ast_facts=None,
            diagnostics=[],
        )

    # Step 3: Extract AST facts and run isolated Ruff linter
    ast_facts = orchestrator.extract_ast_facts(target, source_text=source_text)
    linter_diags = orchestrator.run_diagnostics(target, source_text=source_text)

    return AnalysisReport(
        target=target,
        total_lines=total_lines,
        syntax_valid=True,
        syntax_diagnostics=[],
        ast_facts=ast_facts,
        diagnostics=linter_diags,
    )


def collect_debug_evidence(
    orchestrator: Orchestrator,
    target: TargetRecord,
    target_args: Sequence[str] | None = None,
    stdin_file: str | Path | None = None,
    timeout: float | None = None,
    fail_on_job_failure: bool = False,
) -> ExecutionResult:
    """Execute target under controlled runtime limits and collect traceback evidence.

    In accordance with the Runtime and Isolation Contract:
    - Runs the relocated temporary session copy under -E -B -P.
    - Sets cwd to the user invocation directory.
    - Drains stdout/stderr asynchronously under output byte caps and wall-clock timeout.
    - Parses execution tracebacks into structured frames, classifying target vs external.
    - Normalizes session copy paths back to canonical user target paths.
    - Extracts normalized error signatures.

    Args:
        orchestrator: Active agent orchestrator.
        target: Validated target file metadata.
        target_args: Optional CLI arguments passed after '--' to the target script.
        stdin_file: Optional file path supplying standard input.
        timeout: Optional wall-clock timeout override.
        fail_on_job_failure: Whether to fail closed on Job Object creation/assignment failure.

    Returns:
        ExecutionResult containing exit code, outputs, elapsed time,
        parsed TracebackFrames, and normalized ErrorSignature.
    """
    # Validate that target language is supported by an adapter
    orchestrator.resolve_adapter(target)

    session = orchestrator.session
    if session is not None:
        session.initialize()
        return _execute_and_parse(
            target=target,
            session_target=session.session_target_file,
            target_args=target_args,
            stdin_file=stdin_file,
            timeout=timeout,
            fail_on_job_failure=fail_on_job_failure,
        )

    with Session(target_record=target) as temp_session:
        temp_session.initialize()
        return _execute_and_parse(
            target=target,
            session_target=temp_session.session_target_file,
            target_args=target_args,
            stdin_file=stdin_file,
            timeout=timeout,
            fail_on_job_failure=fail_on_job_failure,
        )


def _execute_and_parse(
    target: TargetRecord,
    session_target: Path,
    target_args: Sequence[str] | None = None,
    stdin_file: str | Path | None = None,
    timeout: float | None = None,
    fail_on_job_failure: bool = False,
) -> ExecutionResult:
    """Subprocess execution helper for debug evidence collection."""
    req = build_execution_request(
        target_path=session_target,
        args=list(target_args) if target_args else None,
        stdin_file=stdin_file,
        cwd=Path.cwd(),
    )

    limits = (
        ExecutionLimits(timeout_seconds=timeout, fail_on_job_failure=fail_on_job_failure)
        if timeout is not None
        else ExecutionLimits(fail_on_job_failure=fail_on_job_failure)
    )
    raw_result = run_execution_request(req, limits=limits)

    frames, sig = parse_traceback(
        raw_result.stderr,
        target_path=target.path,
        session_target_path=session_target,
    )

    if raw_result.timed_out and sig is None:
        sig = ErrorSignature(
            exception_type="TimeoutExpired",
            normalized_message=f"Execution exceeded timeout limit ({limits.timeout_seconds:.1f}s).",
            top_target_file=target.path,
            top_target_line=None,
        )
    elif raw_result.exit_code != 0 and sig is None:
        sig = ErrorSignature(
            exception_type="SystemExit",
            normalized_message=f"Process exited with non-zero code {raw_result.exit_code}.",
            top_target_file=target.path,
            top_target_line=None,
        )

    return ExecutionResult(
        exit_code=raw_result.exit_code,
        stdout=raw_result.stdout,
        stderr=raw_result.stderr,
        duration_seconds=raw_result.duration_seconds,
        timed_out=raw_result.timed_out,
        output_truncated=raw_result.output_truncated,
        peak_process_tree_rss_bytes=raw_result.peak_process_tree_rss_bytes,
        approximate_peak_process_tree_rss_bytes=raw_result.approximate_peak_process_tree_rss_bytes,
        memory_metrics=raw_result.memory_metrics,
        execution_backend=raw_result.execution_backend,
        error_signature=sig,
        frames=frames,
    )
