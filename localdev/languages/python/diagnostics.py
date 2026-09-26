"""Isolated Ruff diagnostics for Python targets with zero project cache pollution.

Executes:
    ruff check --isolated --no-cache --output-format=json <session_target_copy.py>

Guarantees:
- Defense-in-depth single-file isolation: runs on an isolated copy outside the project tree.
- Flag `--isolated`: completely ignores pyproject.toml, ruff.toml, and .ruff.toml in the target's tree.
- Flag `--no-cache`: strictly suppresses all Ruff cache generation and updates.
- Zero cache pollution: leaves zero .ruff_cache directories in the project or its parent tree.
- Normalizes Ruff JSON findings into DiagnosticRecord schemas.
- Handles timeouts (default: 5.0s) and missing binaries gracefully without crashing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from localdev.constants import DEFAULT_RUFF_TIMEOUT_SECONDS
from localdev.patching.edit_schema import EditOperation
from localdev.schemas import DiagnosticRecord, SeverityEnum

if TYPE_CHECKING:
    from localdev.schemas import TargetRecord


def find_ruff_binary() -> Path | None:
    """Locate the ruff executable in the current virtualenv or system PATH."""
    # 1. Check current virtualenv / sys.prefix
    scripts_dir = Path(sys.prefix) / ("Scripts" if sys.platform == "win32" else "bin")
    exe_name = "ruff.exe" if sys.platform == "win32" else "ruff"
    candidate = scripts_dir / exe_name
    if candidate.is_file():
        return candidate

    # 2. Check shutil.which
    which_path = shutil.which("ruff")
    if which_path:
        return Path(which_path)

    if sys.platform == "win32":
        which_exe = shutil.which("ruff.exe")
        if which_exe:
            return Path(which_exe)

    return None


def parse_ruff_json(raw_json: str, default_source: str = "ruff") -> list[DiagnosticRecord]:
    """Parse raw Ruff JSON stdout into a list of normalized DiagnosticRecord instances."""
    raw_json = raw_json.strip()
    if not raw_json or raw_json == "[]":
        return []

    try:
        items = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        return [
            DiagnosticRecord(
                source=default_source,
                code="MalformedOutput",
                message=f"Failed to parse Ruff JSON output: {exc}",
                severity=SeverityEnum.WARNING,
                start_line=1,
                start_col=1,
                end_line=1,
                end_col=1,
                fix_available=False,
            )
        ]

    if not isinstance(items, list):
        return []

    records: list[DiagnosticRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue

        code = str(item.get("code") or item.get("name") or "RUFF")
        message = str(item.get("message") or "Lint finding")

        sev_str = str(item.get("severity") or "warning").lower()
        if sev_str == "error":
            severity = SeverityEnum.ERROR
        elif sev_str == "info":
            severity = SeverityEnum.INFO
        else:
            severity = SeverityEnum.WARNING

        loc = item.get("location") or {}
        start_line = max(1, loc.get("row") or 1)
        start_col = max(1, loc.get("column") or 1)

        end_loc = item.get("end_location") or {}
        end_line = max(start_line, end_loc.get("row") or start_line)
        end_col = end_loc.get("column") or start_col
        if end_line == start_line:
            end_col = max(start_col, end_col)

        has_fix = bool(item.get("fix"))

        records.append(
            DiagnosticRecord(
                source=default_source,
                code=code,
                message=message,
                severity=severity,
                start_line=start_line,
                start_col=start_col,
                end_line=end_line,
                end_col=end_col,
                fix_available=has_fix,
            )
        )

    return records


def audit_zero_ruff_cache(directory: Path | str) -> list[Path]:
    """Scan directory and return all .ruff_cache directories found, if any."""
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return []
    return list(dir_path.rglob(".ruff_cache"))


def run_ruff_diagnostics(
    target_path: Path | str | None = None,
    *,
    timeout_seconds: float = DEFAULT_RUFF_TIMEOUT_SECONDS,
    source_text: str | None = None,
    ruff_binary: Path | str | None = None,
) -> list[DiagnosticRecord]:
    """Run Ruff linter in strict single-file isolation.

    Enforces defense-in-depth:
    1. Copies the target or writes source_text to a temporary isolated workspace.
    2. Runs with `--isolated` to ignore all workspace configurations.
    3. Runs with `--no-cache` to suppress cache directory generation.
    4. Executes via `shell=False`.
    5. Safely cleans up isolated workspace after execution.

    Args:
        target_path: Path to target file on disk (optional if source_text is provided).
        timeout_seconds: Subprocess wall-clock timeout in seconds (default: 5.0).
        source_text: Optional explicit source code string.
        ruff_binary: Optional path to ruff executable for testing or custom environments.

    Returns:
        List of normalized DiagnosticRecord objects.
    """
    resolved_bin = Path(ruff_binary) if ruff_binary else find_ruff_binary()
    if resolved_bin is None or not resolved_bin.is_file():
        return [
            DiagnosticRecord(
                source="ruff",
                code="RuffMissing",
                message="Ruff binary executable not found in active environment or PATH.",
                severity=SeverityEnum.WARNING,
                start_line=1,
                start_col=1,
                end_line=1,
                end_col=1,
                fix_available=False,
            )
        ]

    with tempfile.TemporaryDirectory(prefix="localdev_ruff_") as temp_dir:
        temp_dir_path = Path(temp_dir)
        temp_file = temp_dir_path / "isolated_target.py"

        if source_text is not None:
            temp_file.write_text(source_text, encoding="utf-8")
        elif target_path is not None:
            tgt = Path(target_path)
            if not tgt.is_file():
                return []
            try:
                temp_file.write_bytes(tgt.read_bytes())
            except OSError as exc:
                return [
                    DiagnosticRecord(
                        source="ruff",
                        code="IOError",
                        message=f"Failed to read target file for linting: {exc}",
                        severity=SeverityEnum.ERROR,
                        start_line=1,
                        start_col=1,
                        end_line=1,
                        end_col=1,
                        fix_available=False,
                    )
                ]
        else:
            return []

        cmd = [
            str(resolved_bin),
            "check",
            "--isolated",
            "--no-cache",
            "--output-format=json",
            str(temp_file),
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                shell=False,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return [
                DiagnosticRecord(
                    source="ruff",
                    code="Timeout",
                    message=f"Ruff check timed out after {timeout_seconds:.1f}s",
                    severity=SeverityEnum.WARNING,
                    start_line=1,
                    start_col=1,
                    end_line=1,
                    end_col=1,
                    fix_available=False,
                )
            ]
        except (OSError, FileNotFoundError) as exc:
            return [
                DiagnosticRecord(
                    source="ruff",
                    code="RuffExecutionError",
                    message=f"Failed to execute Ruff binary: {exc}",
                    severity=SeverityEnum.WARNING,
                    start_line=1,
                    start_col=1,
                    end_line=1,
                    end_col=1,
                    fix_available=False,
                )
            ]

        # Ruff returns 0 on clean, 1 on finding lint violations
        if proc.returncode in (0, 1):
            return parse_ruff_json(proc.stdout)

        # Non-standard exit code (e.g. 2 for CLI usage/crash)
        if proc.stdout.strip().startswith("[") or proc.stdout.strip().startswith("{"):
            parsed = parse_ruff_json(proc.stdout)
            if parsed:
                return parsed

        err_msg = proc.stderr.strip() or f"Ruff exited with code {proc.returncode}"
        return [
            DiagnosticRecord(
                source="ruff",
                code="RuffError",
                message=err_msg,
                severity=SeverityEnum.WARNING,
                start_line=1,
                start_col=1,
                end_line=1,
                end_col=1,
                fix_available=False,
            )
        ]


def run_target_diagnostics(
    target: TargetRecord,
    source_text: str | None = None,
    timeout_seconds: float = DEFAULT_RUFF_TIMEOUT_SECONDS,
) -> list[DiagnosticRecord]:
    """Run isolated Ruff diagnostics for a TargetRecord."""
    return run_ruff_diagnostics(
        target_path=target.absolute_path,
        source_text=source_text,
        timeout_seconds=timeout_seconds,
    )


class DiagnosticDiffResult(BaseModel):
    """Differential comparison between baseline and candidate static diagnostics."""

    model_config = ConfigDict(extra="forbid")

    pre_existing: list[DiagnosticRecord] = Field(
        default_factory=list,
        description="Diagnostics present in baseline that persist in candidate (shifted or unchanged).",
    )
    eliminated: list[DiagnosticRecord] = Field(
        default_factory=list,
        description="Diagnostics present in baseline that were eliminated by the patch.",
    )
    introduced: list[DiagnosticRecord] = Field(
        default_factory=list,
        description="Diagnostics present in candidate that were newly introduced by the patch.",
    )
    line_shifts: dict[int, int | None] = Field(
        default_factory=dict,
        description="Mapping of baseline diagnostic start lines to shifted candidate lines.",
    )
    baseline_count: int = Field(default=0, ge=0)
    candidate_count: int = Field(default=0, ge=0)


class LevelAResult(BaseModel):
    """Result of Validation Level A (Static validity) evaluation."""

    model_config = ConfigDict(extra="forbid")

    passed: bool = Field(description="True if candidate satisfies Validation Level A.")
    syntax_valid: bool = Field(description="True if candidate syntax parsed cleanly.")
    syntax_diagnostics: list[DiagnosticRecord] = Field(
        default_factory=list, description="Syntax error diagnostics if candidate parsing failed."
    )
    diagnostic_diff: DiagnosticDiffResult = Field(
        description="Differential static analysis findings between baseline and candidate."
    )
    targeted_eliminated: bool = Field(
        default=True, description="True if all targeted static diagnostics were eliminated."
    )
    failure_reasons: list[str] = Field(
        default_factory=list, description="Human-readable reasons for Level A failure, if any."
    )


def compare_diagnostics(
    baseline_diagnostics: Sequence[DiagnosticRecord],
    candidate_diagnostics: Sequence[DiagnosticRecord],
    edits: Sequence[EditOperation] | None = None,
) -> DiagnosticDiffResult:
    """Compare baseline diagnostics against candidate diagnostics accounting for line shifts.

    Categorizes diagnostics into:
    - pre_existing: Baseline findings that persist in candidate on corresponding shifted lines.
    - eliminated: Baseline findings no longer observed in candidate.
    - introduced: Candidate findings that were not present in baseline.

    Args:
        baseline_diagnostics: Static diagnostics from the pre-patch baseline.
        candidate_diagnostics: Static diagnostics from the post-patch candidate.
        edits: Optional sequence of applied edit operations for line shift mapping.

    Returns:
        DiagnosticDiffResult containing categorized findings and line mapping.
    """
    from localdev.patching.applier import map_baseline_line_to_candidate

    line_shifts: dict[int, int | None] = {}
    shift_map: list[tuple[DiagnosticRecord, int | None]] = []

    for b in baseline_diagnostics:
        if edits is not None:
            cand_line = map_baseline_line_to_candidate(b.start_line, edits)
        else:
            cand_line = b.start_line
        line_shifts[b.start_line] = cand_line
        shift_map.append((b, cand_line))

    cand_pool = list(candidate_diagnostics)
    matched_pairs: list[tuple[DiagnosticRecord, DiagnosticRecord]] = []
    unmatched_baseline: list[DiagnosticRecord] = []

    candidates_for_matching: list[tuple[DiagnosticRecord, int]] = []
    for b, cand_line in shift_map:
        if cand_line is None:
            # Baseline line was directly edited or deleted -> cannot persist as unmodified
            unmatched_baseline.append(b)
        else:
            candidates_for_matching.append((b, cand_line))

    # Match in 3 passes:
    # Pass 1: exact code, line, col, message
    # Pass 2: exact code, line, and either col or message
    # Pass 3: exact code and line
    for pass_num in (1, 2, 3):
        remaining_candidates: list[tuple[DiagnosticRecord, int]] = []
        for b, cand_line in candidates_for_matching:
            match_idx: int | None = None
            for idx, c in enumerate(cand_pool):
                if c.code != b.code or c.start_line != cand_line:
                    continue
                if pass_num == 1:
                    if c.start_col == b.start_col and c.message == b.message:
                        match_idx = idx
                        break
                elif pass_num == 2:
                    if c.start_col == b.start_col or c.message == b.message:
                        match_idx = idx
                        break
                elif pass_num == 3:
                    match_idx = idx
                    break

            if match_idx is not None:
                matched_c = cand_pool.pop(match_idx)
                matched_pairs.append((b, matched_c))
            else:
                remaining_candidates.append((b, cand_line))
        candidates_for_matching = remaining_candidates

    unmatched_baseline.extend(b for b, _ in candidates_for_matching)

    pre_existing = [c for _, c in matched_pairs]
    eliminated = unmatched_baseline
    introduced = list(cand_pool)

    return DiagnosticDiffResult(
        pre_existing=pre_existing,
        eliminated=eliminated,
        introduced=introduced,
        line_shifts=line_shifts,
        baseline_count=len(baseline_diagnostics),
        candidate_count=len(candidate_diagnostics),
    )


def evaluate_level_a(
    candidate_source: str,
    baseline_diagnostics: Sequence[DiagnosticRecord] = (),
    candidate_diagnostics: Sequence[DiagnosticRecord] | None = None,
    edits: Sequence[EditOperation] | None = None,
    targeted_diagnostics: Sequence[DiagnosticRecord | str] | None = None,
    baseline_syntax_valid: bool = True,
    ruff_binary: Path | str | None = None,
    timeout_seconds: float = DEFAULT_RUFF_TIMEOUT_SECONDS,
) -> LevelAResult:
    """Certify Validation Level A (Static validity) for a candidate patch.

    Validation Level A Requirements:
    1. Candidate source parses successfully (ast.parse succeeds with zero syntax errors).
    2. Candidate introduces no new static diagnostics under configured static analysis
       (zero newly introduced Ruff diagnostics or syntax errors compared to baseline).
    3. If the original repair target included a specific static diagnostic (e.g. Ruff error
       or syntax fault), that specific diagnostic must be eliminated.
    4. Runtime-only repair clarity: a runtime-only fix on a Ruff-clean baseline legitimately
       passes Level A without having to eliminate a non-existent static finding, provided it
       parses cleanly and introduces zero new static diagnostics.
    5. Pre-existing findings tolerance: unrelated pre-existing linter warnings do not fail
       Level A as long as they are not newly introduced by the patch.

    Args:
        candidate_source: Reconstructed source code of the candidate.
        baseline_diagnostics: Static diagnostics from the pre-patch baseline.
        candidate_diagnostics: Optional pre-collected candidate diagnostics. If None,
            Ruff diagnostics will be executed on candidate_source if syntax parses cleanly.
        edits: Optional sequence of applied edit operations for line shift mapping.
        targeted_diagnostics: Optional specific static findings or rule codes targeted for elimination.
        baseline_syntax_valid: Whether baseline had valid Python syntax.
        ruff_binary: Optional path to ruff executable.
        timeout_seconds: Subprocess timeout for Ruff invocation.

    Returns:
        LevelAResult containing passed flag, syntax status, diagnostic diff, and details.
    """
    import ast

    syntax_valid = True
    syntax_diagnostics: list[DiagnosticRecord] = []
    failure_reasons: list[str] = []

    try:
        ast.parse(candidate_source)
    except SyntaxError as exc:
        syntax_valid = False
        err_line = exc.lineno or 1
        err_col = exc.offset or 1
        syntax_diag = DiagnosticRecord(
            source="python_syntax",
            code="SyntaxError",
            message=f"{exc.msg} (line {err_line})",
            severity=SeverityEnum.ERROR,
            start_line=max(1, err_line),
            start_col=max(1, err_col),
            end_line=max(1, err_line),
            end_col=max(1, err_col),
            fix_available=False,
        )
        syntax_diagnostics.append(syntax_diag)
        failure_reasons.append(f"Candidate contains syntax error: {exc.msg} (line {err_line})")

    # If candidate diagnostics not supplied, collect them if syntax is valid
    if candidate_diagnostics is not None:
        cand_diags = list(candidate_diagnostics)
    else:
        if not syntax_valid:
            cand_diags = list(syntax_diagnostics)
        else:
            cand_diags = run_ruff_diagnostics(
                source_text=candidate_source,
                ruff_binary=ruff_binary,
                timeout_seconds=timeout_seconds,
            )

    diff = compare_diagnostics(
        baseline_diagnostics=baseline_diagnostics,
        candidate_diagnostics=cand_diags,
        edits=edits,
    )

    if diff.introduced:
        introduced_codes = [d.code for d in diff.introduced]
        failure_reasons.append(
            f"Candidate introduced {len(diff.introduced)} new static diagnostic(s): {introduced_codes}"
        )

    targeted_eliminated = True
    if targeted_diagnostics:
        for targeted in targeted_diagnostics:
            if isinstance(targeted, DiagnosticRecord):
                eliminated_match = any(
                    e.code == targeted.code and e.start_line == targeted.start_line
                    for e in diff.eliminated
                )
                if not eliminated_match:
                    targeted_eliminated = False
                    failure_reasons.append(
                        f"Targeted diagnostic {targeted.code} at line {targeted.start_line} was not eliminated."
                    )
            elif isinstance(targeted, str):
                if targeted == "SyntaxError" and not baseline_syntax_valid and syntax_valid:
                    continue
                eliminated_match = any(e.code == targeted for e in diff.eliminated)
                still_present = any(c.code == targeted for c in cand_diags)
                if not eliminated_match and still_present:
                    targeted_eliminated = False
                    failure_reasons.append(
                        f"Targeted diagnostic code '{targeted}' was not eliminated."
                    )

    if not baseline_syntax_valid and not syntax_valid:
        failure_reasons.append("Baseline syntax error was not eliminated by candidate.")

    passed = (
        syntax_valid
        and len(diff.introduced) == 0
        and targeted_eliminated
    )

    return LevelAResult(
        passed=passed,
        syntax_valid=syntax_valid,
        syntax_diagnostics=syntax_diagnostics,
        diagnostic_diff=diff,
        targeted_eliminated=targeted_eliminated,
        failure_reasons=failure_reasons,
    )

