"""Integration tests for isolated Ruff diagnostics and zero project cache pollution.

Verifies:
1. Ruff executes against single-file target copies with --isolated and --no-cache.
2. Conflicting pyproject.toml and ruff.toml configurations in target project are completely ignored.
3. Post-execution filesystem audit confirms ZERO .ruff_cache directories are created.
4. Clean files produce zero diagnostics.
5. Files with lint violations produce normalized DiagnosticRecords.
6. Timeout and missing-binary edge cases are handled safely.
7. PythonAdapter.run_diagnostics integration.
"""

from __future__ import annotations

from pathlib import Path

from localdev.agent.permissions import validate_target
from localdev.languages.python.adapter import PythonAdapter
from localdev.languages.python.diagnostics import (
    audit_zero_ruff_cache,
    run_ruff_diagnostics,
    run_target_diagnostics,
)
from localdev.schemas import SeverityEnum


def test_ruff_clean_file_zero_findings_zero_cache(tmp_path: Path) -> None:
    project_dir = tmp_path / "user_project"
    project_dir.mkdir()

    clean_file = project_dir / "clean.py"
    clean_file.write_text("def add(x: int, y: int) -> int:\n    return x + y\n", encoding="utf-8")

    diags = run_ruff_diagnostics(clean_file)
    assert diags == []

    # Audit filesystem for cache pollution
    assert audit_zero_ruff_cache(project_dir) == []
    assert audit_zero_ruff_cache(tmp_path) == []


def test_ruff_findings_normalized_zero_cache(tmp_path: Path) -> None:
    project_dir = tmp_path / "user_project"
    project_dir.mkdir()

    lint_file = project_dir / "dirty.py"
    lint_file.write_text("import os\nimport sys\nx = 1\n", encoding="utf-8")

    diags = run_ruff_diagnostics(lint_file)
    assert len(diags) > 0

    codes = [d.code for d in diags]
    assert "F401" in codes  # Unused imports

    # Check normalization attributes
    f401_diag = next(d for d in diags if d.code == "F401")
    assert f401_diag.source == "ruff"
    assert f401_diag.severity in (SeverityEnum.ERROR, SeverityEnum.WARNING)
    assert f401_diag.start_line in (1, 2)
    assert f401_diag.start_col >= 1
    assert f401_diag.end_line >= f401_diag.start_line

    # Audit filesystem for zero cache pollution
    assert audit_zero_ruff_cache(project_dir) == []
    assert audit_zero_ruff_cache(tmp_path) == []


def test_conflicting_pyproject_toml_ignored(tmp_path: Path) -> None:
    """Verify that a pyproject.toml beside the target is ignored due to --isolated."""
    project_dir = tmp_path / "user_project"
    project_dir.mkdir()

    # Place conflicting pyproject.toml that disables F401 (unused import)
    pyproject = project_dir / "pyproject.toml"
    pyproject.write_text(
        """
[tool.ruff.lint]
ignore = ["F401", "I001"]
""",
        encoding="utf-8",
    )

    lint_file = project_dir / "target.py"
    lint_file.write_text("import sys\n", encoding="utf-8")

    diags = run_ruff_diagnostics(lint_file)
    codes = [d.code for d in diags]

    # Because --isolated is passed, F401 must still be reported despite pyproject.toml ignore!
    assert "F401" in codes

    # Audit filesystem for zero cache pollution
    assert audit_zero_ruff_cache(project_dir) == []


def test_conflicting_ruff_toml_ignored(tmp_path: Path) -> None:
    """Verify that a ruff.toml beside the target is ignored due to --isolated."""
    project_dir = tmp_path / "user_project"
    project_dir.mkdir()

    ruff_toml = project_dir / "ruff.toml"
    ruff_toml.write_text(
        """
[lint]
ignore = ["F401"]
""",
        encoding="utf-8",
    )

    lint_file = project_dir / "target.py"
    lint_file.write_text("import os\n", encoding="utf-8")

    diags = run_ruff_diagnostics(lint_file)
    codes = [d.code for d in diags]

    # F401 must still be caught
    assert "F401" in codes
    assert audit_zero_ruff_cache(project_dir) == []


def test_timeout_handling(tmp_path: Path) -> None:
    """Verify timeout expiration is handled gracefully without crashing."""
    target_file = tmp_path / "target.py"
    target_file.write_text("x = 1\n", encoding="utf-8")

    # Set ridiculously tiny timeout to force TimeoutExpired
    diags = run_ruff_diagnostics(target_file, timeout_seconds=0.0000001)
    assert len(diags) == 1
    assert diags[0].code == "Timeout"
    assert diags[0].source == "ruff"
    assert diags[0].severity == SeverityEnum.WARNING


def test_missing_binary_handling(tmp_path: Path) -> None:
    """Verify missing binary is handled cleanly with a warning diagnostic."""
    target_file = tmp_path / "target.py"
    target_file.write_text("x = 1\n", encoding="utf-8")

    diags = run_ruff_diagnostics(
        target_file,
        ruff_binary=tmp_path / "non_existent_ruff_executable.exe",
    )
    assert len(diags) == 1
    assert diags[0].code == "RuffMissing"
    assert diags[0].severity == SeverityEnum.WARNING


def test_source_text_override() -> None:
    """Verify source_text override works without target file on disk."""
    diags = run_ruff_diagnostics(source_text="import math\n")
    codes = [d.code for d in diags]
    assert "F401" in codes


def test_adapter_run_diagnostics_integration(tmp_path: Path) -> None:
    """Verify PythonAdapter.run_diagnostics delegates cleanly."""
    adapter = PythonAdapter()

    clean_file = tmp_path / "clean.py"
    clean_file.write_text("y = 2\n", encoding="utf-8")
    clean_target = validate_target(str(clean_file))

    assert adapter.run_diagnostics(clean_target) == []

    dirty_file = tmp_path / "dirty.py"
    dirty_file.write_text("import collections\n", encoding="utf-8")
    dirty_target = validate_target(str(dirty_file))

    dirty_diags = adapter.run_diagnostics(dirty_target)
    assert any(d.code == "F401" for d in dirty_diags)

    # Verify zero cache pollution in tmp_path
    assert audit_zero_ruff_cache(tmp_path) == []


def test_run_target_diagnostics_helper(tmp_path: Path) -> None:
    """Verify run_target_diagnostics directly against TargetRecord."""
    f = tmp_path / "mod.py"
    f.write_text("import sys\n", encoding="utf-8")
    target = validate_target(str(f))
    diags = run_target_diagnostics(target)
    assert any(d.code == "F401" for d in diags)
    assert audit_zero_ruff_cache(tmp_path) == []

