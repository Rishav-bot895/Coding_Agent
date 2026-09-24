"""Integration tests for the deterministic 'localdev analyse' command.

Verifies:
1. Clean files: AST extraction, zero diagnostics, EXIT_SUCCESS (0).
2. Files with linter warnings: normalized findings, non-fatal, EXIT_SUCCESS (0).
3. Syntax-broken files: short-circuits AST and Ruff, EXIT_TARGET_FAILURE (1).
4. Oversized files: rejects > 256 KB with EXIT_TARGET_IO_ERROR (3).
5. Non-existent files: rejects with EXIT_TARGET_IO_ERROR (3).
6. Unsupported languages: safely abstains with EXIT_ABSTENTION (6).
7. JSON envelopes: preserves raw structure, validation of schema.
8. Terminal output: sanitized, structured banners and sections.
9. Zero temporary artifacts: no .ruff_cache, .pyc, or __pycache__ left in project.
10. Subprocess execution via python -m localdev.cli analyse.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from localdev.cli import main
from localdev.constants import (
    EXIT_ABSTENTION,
    EXIT_SUCCESS,
    EXIT_TARGET_FAILURE,
    EXIT_TARGET_IO_ERROR,
    MAX_SOURCE_SIZE_BYTES,
)
from localdev.languages.python.diagnostics import audit_zero_ruff_cache


def test_analyse_terminal_clean_file(tmp_path: Path) -> None:
    """Verify 'localdev analyse' on a clean file returns 0 and outputs full static evidence."""
    target_file = tmp_path / "math_ops.py"
    target_file.write_text(
        'def add(a: int, b: int) -> int:\n    """Add two numbers."""\n    return a + b\n',
        encoding="utf-8",
    )

    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(["analyse", str(target_file)])

    assert exit_code == EXIT_SUCCESS
    out = stdout.getvalue()
    assert "=== localdev ANALYSE [SUCCESS] ===" in out
    assert "Status:                VALID" in out
    assert "add(a, b)" in out
    assert "CLEAN (zero diagnostic findings)" in out

    # Verify zero cache pollution in project directory
    assert audit_zero_ruff_cache(tmp_path) == []
    assert list(tmp_path.rglob("*.pyc")) == []
    assert list(tmp_path.rglob("__pycache__")) == []


def test_analyse_json_clean_file(tmp_path: Path) -> None:
    """Verify 'localdev analyse --json' produces compliant RFC 8259 JsonEnvelope."""
    target_file = tmp_path / "service.py"
    target_file.write_text(
        "class Worker:\n    def execute(self) -> None:\n        pass\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(["analyse", "--json", str(target_file)])

    assert exit_code == EXIT_SUCCESS
    payload = json.loads(stdout.getvalue())

    assert payload["schema_version"] == "1.0"
    assert payload["command"] == "analyse"
    assert payload["success"] is True
    assert payload["data"]["syntax_valid"] is True
    assert payload["data"]["syntax_diagnostics"] == []
    assert payload["data"]["diagnostics"] == []

    # Check AST facts
    ast_facts = payload["data"]["ast_facts"]
    assert ast_facts is not None
    assert len(ast_facts["classes"]) == 1
    assert ast_facts["classes"][0]["name"] == "Worker"
    assert len(ast_facts["functions"]) == 1
    assert ast_facts["functions"][0]["qualified_name"] == "Worker.execute"


def test_analyse_terminal_linter_findings(tmp_path: Path) -> None:
    """Verify linter warnings are reported in terminal without causing command failure."""
    target_file = tmp_path / "dirty.py"
    target_file.write_text("import sys\nimport os\nx = 1\n", encoding="utf-8")

    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(["analyse", str(target_file)])

    assert exit_code == EXIT_SUCCESS
    out = stdout.getvalue()
    assert "=== localdev ANALYSE [SUCCESS] ===" in out
    assert "Total Findings:" in out
    assert "F401" in out

    # Verify zero cache pollution
    assert audit_zero_ruff_cache(tmp_path) == []


def test_analyse_json_linter_findings(tmp_path: Path) -> None:
    """Verify linter warnings are structured into diagnostics array in JSON."""
    target_file = tmp_path / "dirty.py"
    target_file.write_text("import os\n", encoding="utf-8")

    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(["analyse", "--json", str(target_file)])

    assert exit_code == EXIT_SUCCESS
    payload = json.loads(stdout.getvalue())

    assert payload["success"] is True
    assert payload["data"]["syntax_valid"] is True
    diags = payload["data"]["diagnostics"]
    assert len(diags) > 0
    assert any(d["code"] == "F401" for d in diags)


def test_analyse_syntax_broken_short_circuit_terminal(tmp_path: Path) -> None:
    """Verify syntax error short-circuits AST and Ruff, exiting with code 1."""
    target_file = tmp_path / "broken.py"
    target_file.write_text("def broken_function(\n", encoding="utf-8")

    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(["analyse", str(target_file)])

    assert exit_code == EXIT_TARGET_FAILURE
    out = stdout.getvalue()
    assert "=== localdev ANALYSE [FAILED] ===" in out
    assert "Status:                FAILED" in out
    assert "Syntax Errors:" in out
    assert "Syntax failure short-circuited AST and linter diagnostics" in out
    # Ensure Ruff section was omitted
    assert "Static Diagnostics (Ruff)" not in out


def test_analyse_syntax_broken_short_circuit_json(tmp_path: Path) -> None:
    """Verify JSON output on syntax error has success=False, ast_facts=null, diagnostics=[]."""
    target_file = tmp_path / "broken.py"
    target_file.write_text("def syntax_error)\n", encoding="utf-8")

    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(["analyse", "--json", str(target_file)])

    assert exit_code == EXIT_TARGET_FAILURE
    payload = json.loads(stdout.getvalue())

    assert payload["success"] is False
    assert payload["data"]["syntax_valid"] is False
    assert payload["data"]["ast_facts"] is None
    assert payload["data"]["diagnostics"] == []
    assert len(payload["data"]["syntax_diagnostics"]) > 0
    assert len(payload["errors"]) > 0


def test_analyse_oversized_file(tmp_path: Path) -> None:
    """Verify oversized file (> 256 KB) is rejected with EXIT_TARGET_IO_ERROR (code 3)."""
    target_file = tmp_path / "huge.py"
    target_file.write_bytes(b"x = 1\n" * ((MAX_SOURCE_SIZE_BYTES // 6) + 10))

    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(["analyse", str(target_file)])

    assert exit_code == EXIT_TARGET_IO_ERROR
    assert "exceeds maximum limit" in stderr.getvalue()


def test_analyse_nonexistent_file(tmp_path: Path) -> None:
    """Verify non-existent file exits with code 3."""
    target_file = tmp_path / "ghost.py"

    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(["analyse", str(target_file)])

    assert exit_code == EXIT_TARGET_IO_ERROR


def test_analyse_unsupported_language(tmp_path: Path) -> None:
    """Verify unsupported file types exit with EXIT_ABSTENTION (code 6)."""
    target_file = tmp_path / "page.html"
    target_file.write_text("<html><body>Hello</body></html>", encoding="utf-8")

    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(["analyse", str(target_file)])

    assert exit_code == EXIT_ABSTENTION
    assert "is not supported by any registered language adapter" in stderr.getvalue()


def test_analyse_subprocess_invocation(tmp_path: Path) -> None:
    """Verify end-to-end execution of 'python -m localdev.cli analyse' via subprocess."""
    target_file = tmp_path / "sub_test.py"
    target_file.write_text("def test_func():\n    return 42\n", encoding="utf-8")

    res = subprocess.run(
        [sys.executable, "-m", "localdev.cli", "analyse", "--json", str(target_file)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert res.returncode == EXIT_SUCCESS
    data = json.loads(res.stdout)
    assert data["command"] == "analyse"
    assert data["success"] is True
    assert data["data"]["syntax_valid"] is True
    assert len(data["data"]["ast_facts"]["functions"]) == 1
    assert data["data"]["ast_facts"]["functions"][0]["name"] == "test_func"
