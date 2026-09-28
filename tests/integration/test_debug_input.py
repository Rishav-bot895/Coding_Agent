"""Integration tests for debug command with standard input handling."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from localdev.cli import main
from localdev.constants import EXIT_SUCCESS


def test_debug_with_input_flag(tmp_path: Path) -> None:
    """Verify 'debug -i <string>' feeds input data correctly."""
    script = tmp_path / "calc_sum.py"
    script.write_text(
        "a = int(input('a: '))\n"
        "b = int(input('b: '))\n"
        "print(f'SUM={a + b}')\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(["debug", str(script), "-i", "15\\n25"])

    assert code == EXIT_SUCCESS
    assert "SUM=40" in stdout.getvalue()


def test_debug_with_long_input_flag(tmp_path: Path) -> None:
    """Verify 'debug --input <string>' feeds input data correctly with JSON output."""
    script = tmp_path / "greeting.py"
    script.write_text(
        "name = input('Name: ')\n"
        "print(f'Hello, {name}!')\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(["debug", str(script), "--input", "Alice", "--json"])

    assert code == EXIT_SUCCESS
    env = json.loads(stdout.getvalue())
    assert env["success"] is True
    assert "Hello, Alice!" in env["data"]["stdout"]


def test_debug_demo_test1_with_input() -> None:
    """Verify demo/test1.py runs without timeout when input is supplied."""
    demo_script = Path("demo/test1.py")
    if not demo_script.is_file():
        return

    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(["debug", str(demo_script), "-i", "16", "--json"])

    assert code == EXIT_SUCCESS
    env = json.loads(stdout.getvalue())
    assert env["success"] is True
    assert "Iterations: 4" in env["data"]["stdout"]


def test_debug_piped_input_via_subprocess(tmp_path: Path) -> None:
    """Verify piped input into 'localdev.cli debug' via real subprocess."""
    script = tmp_path / "echo_in.py"
    script.write_text(
        "val = input('Input: ')\n"
        "print(f'Received: {val}')\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, "-m", "localdev.cli", "debug", str(script)],
        input="PipedValue\n",
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == EXIT_SUCCESS
    assert "Received: PipedValue" in proc.stdout

