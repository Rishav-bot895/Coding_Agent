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
from pathlib import Path
from typing import TYPE_CHECKING

from localdev.constants import DEFAULT_RUFF_TIMEOUT_SECONDS
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
