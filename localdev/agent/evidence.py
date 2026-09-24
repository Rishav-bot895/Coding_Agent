"""Evidence collection and deterministic static analysis workflows.

Coordinates deterministic static evidence collection without executing code:
1. Validates target and checks syntax.
2. Short-circuits immediately if syntax errors are detected (skips AST and Ruff).
3. If syntax is valid, extracts bounded AST facts and runs isolated Ruff diagnostics.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from localdev.schemas import AnalysisReport, SeverityEnum

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

