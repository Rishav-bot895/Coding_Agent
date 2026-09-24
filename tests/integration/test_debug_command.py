"""Integration tests for the deterministic 'localdev debug' command.

Verifies:
1. Clean scripts: clean execution, exit 0, captured output, EXIT_SUCCESS (0).
2. Crashing scripts: traceback parsing, error signature, exit code 1, EXIT_TARGET_FAILURE (1).
3. JSON output: RFC 8259 JsonEnvelope with ExecutionResult data.
4. Target argument forwarding via '--'.
5. Stdin redirection via '--stdin-file'.
6. Timeout enforcement and ErrorSignature synthesis.
7. Zero cache pollution: -B prevents __pycache__ generation in target directory.
8. Subprocess invocation via `python -m localdev.cli debug`.
9. Non-existent and unsupported target handling.
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
)


def test_debug_terminal_clean_script(tmp_path: Path) -> None:
    """Verify 'localdev debug' on a clean script returns 0 and outputs summary + stdout."""
    script = tmp_path / "hello.py"
    script.write_text(
        "import sys\nprint('Hello from isolated target!')\nsys.stdout.flush()\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(["debug", str(script)])

    assert exit_code == EXIT_SUCCESS
    out = stdout.getvalue()
    assert "=== localdev DEBUG [SUCCESS] ===" in out
    assert "Status:                SUCCESS" in out
    assert "Exit Code:             0" in out
    assert "Hello from isolated target!" in out


def test_debug_terminal_crash_script(tmp_path: Path) -> None:
    """Verify 'localdev debug' on a crashing script returns 1 and extracts traceback signature."""
    script = tmp_path / "crash.py"
    script.write_text(
        "def compute(x: int) -> int:\n"
        "    return 100 // x\n\n"
        "compute(0)\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(["debug", str(script)])

    assert exit_code == EXIT_TARGET_FAILURE
    out = stdout.getvalue()
    assert "=== localdev DEBUG [FAILED] ===" in out
    assert "Status:                FAILED" in out
    assert "Exit Code:             1" in out
    assert "Exception Type:        ZeroDivisionError" in out
    assert "integer division or modulo by zero" in out
    assert "compute" in out
    assert "return 100 // x" in out


def test_debug_json_clean_script(tmp_path: Path) -> None:
    """Verify 'localdev debug --json' generates valid RFC 8259 JsonEnvelope on success."""
    script = tmp_path / "clean_calc.py"
    script.write_text("print('42')\n", encoding="utf-8")

    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(["debug", "--json", str(script)])

    assert exit_code == EXIT_SUCCESS
    raw_json = stdout.getvalue()
    envelope = json.loads(raw_json)

    assert envelope["command"] == "debug"
    assert envelope["success"] is True
    assert envelope["target_path"] == str(script)
    assert envelope["errors"] == []

    data = envelope["data"]
    assert data["exit_code"] == 0
    assert data["timed_out"] is False
    assert "42" in data["stdout"]
    assert data["error_signature"] is None
    assert data["frames"] == []


def test_debug_json_crash_script(tmp_path: Path) -> None:
    """Verify 'localdev debug --json' outputs error signature and frames on crash."""
    script = tmp_path / "fail_script.py"
    script.write_text(
        "def bad():\n"
        "    raise ValueError('invalid parameter value')\n\n"
        "bad()\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(["debug", "--json", str(script)])

    assert exit_code == EXIT_TARGET_FAILURE
    envelope = json.loads(stdout.getvalue())

    assert envelope["command"] == "debug"
    assert envelope["success"] is False
    assert "ValueError: invalid parameter value" in envelope["errors"][0]

    data = envelope["data"]
    assert data["exit_code"] != 0
    assert data["timed_out"] is False
    assert data["error_signature"] is not None
    assert data["error_signature"]["exception_type"] == "ValueError"
    assert data["error_signature"]["normalized_message"] == "invalid parameter value"
    assert data["error_signature"]["top_target_file"] == str(script)
    assert data["error_signature"]["top_target_line"] == 2

    frames = data["frames"]
    assert len(frames) >= 1
    target_frames = [f for f in frames if f["is_target"]]
    assert len(target_frames) >= 1
    assert target_frames[-1]["line_number"] == 2


def test_debug_target_arguments(tmp_path: Path) -> None:
    """Verify target arguments after '--' are cleanly forwarded to the target process."""
    script = tmp_path / "echo_args.py"
    script.write_text(
        "import sys\nprint('ARGS:' + ','.join(sys.argv[1:]))\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(["debug", str(script), "--", "apple", "banana", "--count=3"])

    assert exit_code == EXIT_SUCCESS
    assert "ARGS:apple,banana,--count=3" in stdout.getvalue()


def test_debug_stdin_file(tmp_path: Path) -> None:
    """Verify '--stdin-file' redirects input into the target process stdin."""
    stdin_file = tmp_path / "payload.txt"
    stdin_file.write_text("structured input data line 1\nline 2\n", encoding="utf-8")

    script = tmp_path / "read_stdin.py"
    script.write_text(
        "import sys\ncontent = sys.stdin.read()\nprint(f'READ {len(content)} chars')\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(["debug", str(script), "--stdin-file", str(stdin_file)])

    assert exit_code == EXIT_SUCCESS
    assert "READ 36 chars" in stdout.getvalue()


def test_debug_timeout(tmp_path: Path) -> None:
    """Verify '--timeout' terminates hanging processes and records TimeoutExpired."""
    script = tmp_path / "sleep_forever.py"
    script.write_text(
        "import time\ntime.sleep(10.0)\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = main(["debug", "--json", "--timeout", "0.5", str(script)])

    assert exit_code == EXIT_TARGET_FAILURE
    envelope = json.loads(stdout.getvalue())

    assert envelope["success"] is False
    data = envelope["data"]
    assert data["timed_out"] is True
    assert data["error_signature"] is not None
    assert data["error_signature"]["exception_type"] == "TimeoutExpired"


def test_debug_zero_cache_pollution(tmp_path: Path) -> None:
    """Verify -B prevents pyc / __pycache__ generation in target directory."""
    script = tmp_path / "test_module.py"
    script.write_text("print('no pycache')\n", encoding="utf-8")

    stdout = io.StringIO()
    stderr = io.StringIO()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        main(["debug", str(script)])

    pyc_files = list(tmp_path.rglob("*.pyc"))
    pycache_dirs = list(tmp_path.rglob("__pycache__"))
    assert pyc_files == []
    assert pycache_dirs == []


def test_debug_subprocess_invocation(tmp_path: Path) -> None:
    """Verify real subprocess CLI execution via `python -m localdev.cli debug`."""
    script = tmp_path / "sub_test.py"
    script.write_text("print('cli subprocess works')\n", encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "-m", "localdev.cli", "debug", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == EXIT_SUCCESS
    assert "=== localdev DEBUG [SUCCESS] ===" in proc.stdout
    assert "cli subprocess works" in proc.stdout


def test_debug_nonexistent_and_unsupported(tmp_path: Path) -> None:
    """Verify error handling on non-existent files and unsupported file types."""
    missing = tmp_path / "non_existent.py"
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(["debug", str(missing)])
    assert code == EXIT_TARGET_IO_ERROR

    unsupported = tmp_path / "doc.unknown"
    unsupported.write_text("plain text", encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(["debug", str(unsupported)])
    assert code == EXIT_ABSTENTION
