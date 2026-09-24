"""Integration tests for 'localdev info' command end-to-end.

Verifies:
1. 'localdev info <target>' outputs structured terminal report with target attributes,
   line count, and language detection results.
2. 'localdev info <target> --json' outputs valid JsonEnvelope[TargetInfoRecord] with 1.0 schema.
3. Non-modifying invariant: target file content, SHA-256, and timestamps remain completely untouched.
4. Non-Python targets report file attributes and 'unsupported' language detection.
5. Unicode-named targets are processed cleanly without encoding errors.
6. Invalid target files exit with EXIT_TARGET_IO_ERROR (code 3) and output clean errors.
7. Subprocess execution via python -m localdev.cli info.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from localdev.cli import main
from localdev.constants import EXIT_SUCCESS, EXIT_TARGET_IO_ERROR

FIXTURES_DIR = Path(__file__).parent.parent / "boundary_samples" / "languages"


def test_info_terminal_valid_python(tmp_path: Path) -> None:
    """Verify 'localdev info' renders full target attributes and language detection to terminal."""
    target_file = tmp_path / "sample.py"
    target_file.write_text("x = 1\ny = 2\nprint(x + y)\n", encoding="utf-8")

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["info", str(target_file)])

    assert exit_code == EXIT_SUCCESS
    out = stdout.getvalue()
    assert "=== localdev INFO [SUCCESS] ===" in out
    assert f"Target: {target_file}" in out
    assert "--- Target File Attributes ---" in out
    assert "Lines:                 3" in out
    assert "Encoding:              utf-8" in out
    assert "--- Language Detection ---" in out
    assert "Language:              python" in out
    assert "Confidence:            CERTAIN" in out


def test_info_json_valid_python(tmp_path: Path) -> None:
    """Verify 'localdev info --json' outputs schema-valid JsonEnvelope[TargetInfoRecord]."""
    target_file = tmp_path / "app.py"
    content = "def hello():\n    return 'world'\n"
    target_file.write_text(content, encoding="utf-8")

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["info", str(target_file), "--json"])

    assert exit_code == EXIT_SUCCESS
    payload = json.loads(stdout.getvalue())
    assert payload["schema_version"] == "1.0"
    assert payload["command"] == "info"
    assert payload["success"] is True
    assert payload["target_path"] == str(target_file)

    data = payload["data"]
    assert data["total_lines"] == 2
    assert data["target"]["file_size_bytes"] == target_file.stat().st_size
    assert data["target"]["encoding"] == "utf-8"
    assert data["detection"]["language"] == "python"
    assert data["detection"]["confidence"] == "CERTAIN"


def test_info_non_modifying_invariant(tmp_path: Path) -> None:
    """Verify 'localdev info' leaves file content, hash, timestamp, and directory unmodified."""
    target_file = tmp_path / "guarded.py"
    target_file.write_text("print('untouched')\n", encoding="utf-8")

    stat_before = target_file.stat()
    bytes_before = target_file.read_bytes()
    dir_entries_before = sorted(p.name for p in tmp_path.iterdir())

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["info", str(target_file)])

    assert exit_code == EXIT_SUCCESS

    stat_after = target_file.stat()
    bytes_after = target_file.read_bytes()
    dir_entries_after = sorted(p.name for p in tmp_path.iterdir())

    assert bytes_after == bytes_before
    assert stat_after.st_mtime_ns == stat_before.st_mtime_ns
    assert stat_after.st_size == stat_before.st_size
    assert dir_entries_after == dir_entries_before


def test_info_unicode_named_file(tmp_path: Path) -> None:
    """Verify 'localdev info' handles targets with spaces and Unicode characters."""
    unicode_file = tmp_path / "targët with spaces αβγ.py"
    unicode_file.write_text("# Unicode script\nval = 42\n", encoding="utf-8")

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["info", str(unicode_file), "--json"])

    assert exit_code == EXIT_SUCCESS
    payload = json.loads(stdout.getvalue())
    assert payload["success"] is True
    assert payload["data"]["detection"]["language"] == "python"


def test_info_non_python_target() -> None:
    """Verify 'localdev info' on non-Python target reports attributes and 'unsupported'."""
    html_target = FIXTURES_DIR / "page.html"

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["info", str(html_target), "--json"])

    assert exit_code == EXIT_SUCCESS
    payload = json.loads(stdout.getvalue())
    assert payload["success"] is True
    assert payload["data"]["detection"]["language"] == "unsupported"
    assert payload["data"]["detection"]["confidence"] == "UNSUPPORTED"


def test_info_missing_file_terminal() -> None:
    """Verify 'localdev info' on missing target prints error and exits with code 3."""
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        exit_code = main(["info", "non_existent_file_12345.py"])

    assert exit_code == EXIT_TARGET_IO_ERROR
    assert "Target file does not exist" in stderr.getvalue()


def test_info_missing_file_json() -> None:
    """Verify 'localdev info --json' on missing target produces valid error envelope."""
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["info", "non_existent_file_12345.py", "--json"])

    assert exit_code == EXIT_TARGET_IO_ERROR
    payload = json.loads(stdout.getvalue())
    assert payload["schema_version"] == "1.0"
    assert payload["command"] == "info"
    assert payload["success"] is False
    assert any("does not exist" in err for err in payload["errors"])


def test_info_subprocess_execution(tmp_path: Path) -> None:
    """Verify 'python -m localdev.cli info' execution via subprocess from shell."""
    target_file = tmp_path / "sub_test.py"
    target_file.write_text("print('subprocess')\n", encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "localdev.cli", "info", str(target_file), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == EXIT_SUCCESS
    payload = json.loads(proc.stdout)
    assert payload["command"] == "info"
    assert payload["success"] is True
    assert payload["data"]["detection"]["confidence"] == "CERTAIN"
