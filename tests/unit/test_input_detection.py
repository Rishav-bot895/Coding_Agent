"""Unit tests for AST input requirement detection and input resolution."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

from localdev.cli import resolve_execution_inputs
from localdev.languages.python.ast_analyser import (
    InputRequirement,
    detect_input_requirements,
)


def test_detect_input_simple_prompt() -> None:
    source = 'name = input("Enter your name: ")\n'
    req = detect_input_requirements(source)
    assert req.requires_input is True
    assert req.prompts == ["Enter your name: "]
    assert req.has_loop_input is False


def test_detect_input_no_prompt() -> None:
    source = "val = input()\n"
    req = detect_input_requirements(source)
    assert req.requires_input is True
    assert req.has_loop_input is False


def test_detect_input_in_for_loop() -> None:
    source = """
n = int(input("Count: "))
items = []
for _ in range(n):
    items.append(input())
"""
    req = detect_input_requirements(source)
    assert req.requires_input is True
    assert req.prompts == ["Count: "]
    assert req.has_loop_input is True


def test_detect_input_in_while_loop() -> None:
    source = """
while True:
    line = input()
    if not line:
        break
"""
    req = detect_input_requirements(source)
    assert req.requires_input is True
    assert req.has_loop_input is True


def test_detect_sys_stdin_read() -> None:
    source = """
import sys
content = sys.stdin.read()
"""
    req = detect_input_requirements(source)
    assert req.requires_input is True
    assert req.has_loop_input is False


def test_detect_no_inputs() -> None:
    source = "print('pure calculation')\nx = 1 + 2\n"
    req = detect_input_requirements(source)
    assert req.requires_input is False
    assert req.prompts == []
    assert req.has_loop_input is False


def test_detect_syntax_error_handled_safely() -> None:
    source = "def broken(\n"
    req = detect_input_requirements(source)
    assert req.requires_input is False


def test_resolve_execution_inputs_cli_flag() -> None:
    data, f = resolve_execution_inputs("dummy.py", input_data="10\\n20\\n30")
    assert data == "10\n20\n30\n"
    assert f is None


def test_resolve_execution_inputs_stdin_file() -> None:
    data, f = resolve_execution_inputs("dummy.py", stdin_file="data.txt")
    assert data is None
    assert f == "data.txt"


def test_resolve_execution_inputs_no_input_needed(tmp_path: Path) -> None:
    script = tmp_path / "calc.py"
    script.write_text("print('no input')\n", encoding="utf-8")
    data, f = resolve_execution_inputs(script)
    assert data is None
    assert f is None


def test_resolve_execution_inputs_interactive_single_prompt(tmp_path: Path) -> None:
    script = tmp_path / "prompt.py"
    script.write_text('n = int(input("Enter n: "))\n', encoding="utf-8")

    stderr_capture = io.StringIO()
    with patch("sys.stdin.isatty", return_value=True), patch("sys.stdin.readline", return_value="42\n"), patch("sys.stderr", stderr_capture):
        data, f = resolve_execution_inputs(script)

    assert data == "42\n"
    assert f is None
    assert "Enter n: " in stderr_capture.getvalue()


def test_resolve_execution_inputs_interactive_loop(tmp_path: Path) -> None:
    script = tmp_path / "loop_in.py"
    script.write_text(
        "n = int(input('Size: '))\n"
        "arr = []\n"
        "for _ in range(n):\n"
        "    arr.append(int(input()))\n",
        encoding="utf-8",
    )

    stderr_capture = io.StringIO()
    inputs = ["3\n", "100\n", "200\n", "300\n", "\n"]
    with patch("sys.stdin.isatty", return_value=True), patch("sys.stdin.readline", side_effect=inputs), patch("sys.stderr", stderr_capture):
        data, f = resolve_execution_inputs(script)

    assert data == "3\n100\n200\n300\n"
    assert f is None
    assert "[localdev] Target requires standard input" in stderr_capture.getvalue()
