"""Integration tests for 'localdev detect' command end-to-end.

Verifies:
1. 'localdev detect <target>' renders language, confidence, and reasons to terminal.
2. 'localdev detect <target> --json' outputs valid JsonEnvelope[DetectionResult].
3. Shebang scripts with and without extensions detected properly.
4. Non-Python targets (e.g. script.js, page.html) classified as unsupported.
5. Non-modifying invariant: target file content, hash, and timestamps remain completely untouched.
6. Missing target files exit with EXIT_TARGET_IO_ERROR (code 3).
7. Subprocess execution via python -m localdev.cli detect.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys

from localdev.cli import main
from localdev.constants import EXIT_SUCCESS, EXIT_TARGET_IO_ERROR

FIXTURES_DIR = Path(__file__).parent.parent / "boundary_samples" / "languages"


def test_detect_terminal_valid_python(tmp_path: Path) -> None:
    """Verify 'localdev detect' outputs formatted detection banner to terminal."""
    target_file = tmp_path / "main.py"
    target_file.write_text("print('hello')\n", encoding="utf-8")

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["detect", str(target_file)])

    assert exit_code == EXIT_SUCCESS
    out = stdout.getvalue()
    assert "=== localdev DETECT [SUCCESS] ===" in out
    assert f"Target: {target_file}" in out
    assert "Language:              python" in out
    assert "Confidence:            CERTAIN" in out
    assert "Detection Reasons:" in out


def test_detect_json_valid_python(tmp_path: Path) -> None:
    """Verify 'localdev detect --json' outputs valid JsonEnvelope[DetectionResult]."""
    target_file = tmp_path / "main.py"
    target_file.write_text("x = 42\n", encoding="utf-8")

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["detect", str(target_file), "--json"])

    assert exit_code == EXIT_SUCCESS
    payload = json.loads(stdout.getvalue())
    assert payload["schema_version"] == "1.0"
    assert payload["command"] == "detect"
    assert payload["success"] is True
    assert payload["target_path"] == str(target_file)

    data = payload["data"]
    assert data["language"] == "python"
    assert data["confidence"] == "CERTAIN"
    assert data["matched_extension"] == ".py"


def test_detect_shebang_script() -> None:
    """Verify 'localdev detect' on extensionless shebang script."""
    target = FIXTURES_DIR / "shebang_script"

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["detect", str(target), "--json"])

    assert exit_code == EXIT_SUCCESS
    payload = json.loads(stdout.getvalue())
    assert payload["data"]["language"] == "python"
    assert payload["data"]["confidence"] == "CERTAIN"
    assert payload["data"]["has_shebang"] is True


def test_detect_non_python_target() -> None:
    """Verify 'localdev detect' on non-Python target returns UNSUPPORTED."""
    target = FIXTURES_DIR / "script.js"

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["detect", str(target), "--json"])

    assert exit_code == EXIT_SUCCESS
    payload = json.loads(stdout.getvalue())
    assert payload["data"]["language"] == "unsupported"
    assert payload["data"]["confidence"] == "UNSUPPORTED"


def test_detect_non_modifying_invariant(tmp_path: Path) -> None:
    """Verify 'localdev detect' leaves file content, hash, timestamp, and directory unmodified."""
    target_file = tmp_path / "guarded.py"
    target_file.write_text("x = 100\n", encoding="utf-8")

    stat_before = target_file.stat()
    bytes_before = target_file.read_bytes()
    dir_entries_before = sorted(p.name for p in tmp_path.iterdir())

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["detect", str(target_file)])

    assert exit_code == EXIT_SUCCESS

    stat_after = target_file.stat()
    bytes_after = target_file.read_bytes()
    dir_entries_after = sorted(p.name for p in tmp_path.iterdir())

    assert bytes_after == bytes_before
    assert stat_after.st_mtime_ns == stat_before.st_mtime_ns
    assert stat_after.st_size == stat_before.st_size
    assert dir_entries_after == dir_entries_before


def test_detect_missing_file_terminal() -> None:
    """Verify 'localdev detect' on missing file outputs error and exits with code 3."""
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        exit_code = main(["detect", "missing_98765.py"])

    assert exit_code == EXIT_TARGET_IO_ERROR
    assert "Target file does not exist" in stderr.getvalue()


def test_detect_missing_file_json() -> None:
    """Verify 'localdev detect --json' on missing file produces valid error envelope."""
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["detect", "missing_98765.py", "--json"])

    assert exit_code == EXIT_TARGET_IO_ERROR
    payload = json.loads(stdout.getvalue())
    assert payload["schema_version"] == "1.0"
    assert payload["command"] == "detect"
    assert payload["success"] is False
    assert any("does not exist" in err for err in payload["errors"])


def test_detect_subprocess_execution(tmp_path: Path) -> None:
    """Verify 'python -m localdev.cli detect' execution via subprocess from shell."""
    target_file = tmp_path / "sub_detect.py"
    target_file.write_text("x = 'sub'\n", encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "localdev.cli", "detect", str(target_file), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == EXIT_SUCCESS
    payload = json.loads(proc.stdout)
    assert payload["command"] == "detect"
    assert payload["success"] is True
    assert payload["data"]["confidence"] == "CERTAIN"
