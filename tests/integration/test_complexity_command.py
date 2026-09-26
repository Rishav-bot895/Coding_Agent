"""Integration tests for the deterministic 'localdev complexity' CLI command.

Verifies:
1. Whole-file summary: reports all top-level functions and class methods with line numbers.
2. Targeted function selector: file.py::function_name evaluates single function.
3. Targeted method selector: file.py::ClassName.method evaluates single method.
4. RFC 8259 compliant JSON envelope output via '--json'.
5. Algorithmic abstention: dynamic loops, recursion, and unknown calls produce EXIT_ABSTENTION (6).
6. Broken syntax target: returns EXIT_TARGET_FAILURE (1).
7. Ambiguous selector: multiple class methods match -> EXIT_CLI_USAGE_ERROR (2).
8. Missing selector: selector not found -> EXIT_CLI_USAGE_ERROR (2).
9. Nested function selector: rejected with EXIT_CLI_USAGE_ERROR (2).
10. Missing target file: returns EXIT_TARGET_IO_ERROR (3).
11. Unsupported language: returns EXIT_ABSTENTION (6).
12. Pure static analysis invariant: never imports or executes target code.
13. Full subprocess CLI invocation via 'python -m localdev.cli complexity'.
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
    EXIT_CLI_USAGE_ERROR,
    EXIT_SUCCESS,
    EXIT_TARGET_FAILURE,
    EXIT_TARGET_IO_ERROR,
)
from localdev.schemas import ComplexityClassEnum, ComplexityReport


def test_complexity_terminal_single_function(tmp_path: Path) -> None:
    """Verify 'localdev complexity' on single function reports time, space, and lines."""
    target_file = tmp_path / "math_ops.py"
    target_file.write_text(
        'def add(a: int, b: int) -> int:\n    """Add two numbers."""\n    return a + b\n',
        encoding="utf-8",
    )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["complexity", f"{target_file}::add"])

    assert exit_code == EXIT_SUCCESS
    out = stdout.getvalue()
    assert "=== localdev COMPLEXITY [SUCCESS] ===" in out
    assert "Time Complexity:       O(1)" in out
    assert "Auxiliary Space:       O(1)" in out
    assert "Output Space:          O(1)" in out
    assert "Confidence:            HIGH" in out
    assert "lines 1-3" in out


def test_complexity_json_single_function(tmp_path: Path) -> None:
    """Verify 'localdev complexity --json' produces compliant RFC 8259 JsonEnvelope."""
    target_file = tmp_path / "data_ops.py"
    target_file.write_text(
        "def find_max(items: list[int]) -> int:\n"
        "    highest = items[0]\n"
        "    for x in items:\n"
        "        if x > highest:\n"
        "            highest = x\n"
        "    return highest\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["complexity", f"{target_file}::find_max", "--json"])

    assert exit_code == EXIT_SUCCESS
    payload = json.loads(stdout.getvalue())

    assert payload["schema_version"] == "1.0"
    assert payload["command"] == "complexity"
    assert payload["success"] is True
    assert payload["target_path"] == f"{target_file}::find_max"

    report = ComplexityReport.model_validate(payload["data"])
    assert report.time_complexity == ComplexityClassEnum.O_N
    assert report.auxiliary_space == ComplexityClassEnum.O_1
    assert report.output_space == ComplexityClassEnum.O_1
    assert report.start_line == 1
    assert report.end_line == 6


def test_complexity_class_method_selector(tmp_path: Path) -> None:
    """Verify selecting a class method via ClassName.method."""
    target_file = tmp_path / "service.py"
    target_file.write_text(
        "class SearchEngine:\n"
        "    def binary_search(self, arr: list[int], target: int, low: int, high: int) -> int:\n"
        "        if low > high:\n"
        "            return -1\n"
        "        mid = (low + high) // 2\n"
        "        if arr[mid] == target:\n"
        "            return mid\n"
        "        if arr[mid] > target:\n"
        "            return self.binary_search(arr, target, low, mid - 1)\n"
        "        return self.binary_search(arr, target, mid + 1, high)\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["complexity", f"{target_file}::SearchEngine.binary_search", "--json"])

    assert exit_code == EXIT_SUCCESS
    payload = json.loads(stdout.getvalue())
    assert payload["success"] is True

    report = ComplexityReport.model_validate(payload["data"])
    assert report.time_complexity == ComplexityClassEnum.O_LOG_N
    assert report.auxiliary_space == ComplexityClassEnum.O_LOG_N


def test_complexity_whole_file_summary(tmp_path: Path) -> None:
    """Verify running without selector provides a summary of all functions and methods."""
    target_file = tmp_path / "multi.py"
    target_file.write_text(
        "def helper(n: int) -> int:\n"
        "    return n * 2\n\n"
        "class Calculator:\n"
        "    def compute(self, items: list[int]) -> int:\n"
        "        total = 0\n"
        "        for x in items:\n"
        "            total += x\n"
        "        return total\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["complexity", str(target_file), "--json"])

    assert exit_code == EXIT_SUCCESS
    payload = json.loads(stdout.getvalue())
    assert payload["success"] is True
    assert isinstance(payload["data"], list)
    assert len(payload["data"]) == 2

    r1 = ComplexityReport.model_validate(payload["data"][0])
    assert r1.target.endswith("helper")
    assert r1.time_complexity == ComplexityClassEnum.O_1

    r2 = ComplexityReport.model_validate(payload["data"][1])
    assert r2.target.endswith("Calculator.compute")
    assert r2.time_complexity == ComplexityClassEnum.O_N


def test_complexity_abstention_dynamic_recursion(tmp_path: Path) -> None:
    """Verify recursion lacking base case causes clean abstention with EXIT_ABSTENTION (6)."""
    target_file = tmp_path / "bad_rec.py"
    target_file.write_text(
        "def loop_rec(n: int) -> int:\n"
        "    return n * loop_rec(n - 1)\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["complexity", f"{target_file}::loop_rec", "--json"])

    assert exit_code == EXIT_ABSTENTION
    payload = json.loads(stdout.getvalue())
    assert payload["success"] is False
    assert payload["data"]["time_complexity"] == "UNKNOWN"
    assert payload["data"]["abstention_reason"] == "DYNAMIC_RECURSION"
    assert len(payload["limitations"]) > 0


def test_complexity_abstention_while_loop(tmp_path: Path) -> None:
    """Verify data-dependent while loop causes abstention with DYNAMIC_BOUNDS (6)."""
    target_file = tmp_path / "dynamic_loop.py"
    target_file.write_text(
        "def dynamic_while(n: int) -> int:\n"
        "    while n > 0:\n"
        "        n -= 1\n"
        "    return n\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["complexity", f"{target_file}::dynamic_while", "--json"])

    assert exit_code == EXIT_ABSTENTION
    payload = json.loads(stdout.getvalue())
    assert payload["success"] is False
    assert payload["data"]["abstention_reason"] == "DYNAMIC_BOUNDS"


def test_complexity_abstention_unknown_call(tmp_path: Path) -> None:
    """Verify unknown user call causes abstention with UNKNOWN_CALL (6)."""
    target_file = tmp_path / "unknown_call.py"
    target_file.write_text(
        "def caller(items: list[int]) -> int:\n"
        "    return external_helper(items)\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["complexity", f"{target_file}::caller", "--json"])

    assert exit_code == EXIT_ABSTENTION
    payload = json.loads(stdout.getvalue())
    assert payload["success"] is False
    assert payload["data"]["abstention_reason"] == "UNKNOWN_CALL"


def test_complexity_syntax_error_target(tmp_path: Path) -> None:
    """Verify syntax error in target causes EXIT_TARGET_FAILURE (1)."""
    target_file = tmp_path / "syntax_error.py"
    target_file.write_text(
        "def broken(\n    pass\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["complexity", str(target_file), "--json"])

    assert exit_code == EXIT_TARGET_FAILURE
    payload = json.loads(stdout.getvalue())
    assert payload["success"] is False
    assert len(payload["errors"]) > 0


def test_complexity_ambiguous_selector(tmp_path: Path) -> None:
    """Verify ambiguous selector matching multiple methods raises EXIT_CLI_USAGE_ERROR (2)."""
    target_file = tmp_path / "ambiguous.py"
    target_file.write_text(
        "class A:\n    def run(self): pass\n\n"
        "class B:\n    def run(self): pass\n",
        encoding="utf-8",
    )

    stderr = io.StringIO()
    with redirect_stderr(stderr):
        exit_code = main(["complexity", f"{target_file}::run"])

    assert exit_code == EXIT_CLI_USAGE_ERROR
    assert "Ambiguous selector" in stderr.getvalue()


def test_complexity_non_existent_selector(tmp_path: Path) -> None:
    """Verify selector not found in target file raises EXIT_CLI_USAGE_ERROR (2)."""
    target_file = tmp_path / "sample.py"
    target_file.write_text(
        "def existing_func(): pass\n",
        encoding="utf-8",
    )

    stderr = io.StringIO()
    with redirect_stderr(stderr):
        exit_code = main(["complexity", f"{target_file}::missing_func"])

    assert exit_code == EXIT_CLI_USAGE_ERROR
    assert "not found in target" in stderr.getvalue()
    assert "existing_func" in stderr.getvalue()


def test_complexity_nested_function_selector_rejected(tmp_path: Path) -> None:
    """Verify selector targeting nested function is rejected with EXIT_CLI_USAGE_ERROR (2)."""
    target_file = tmp_path / "nested.py"
    target_file.write_text(
        "def outer():\n    def inner(): pass\n",
        encoding="utf-8",
    )

    stderr = io.StringIO()
    with redirect_stderr(stderr):
        exit_code = main(["complexity", f"{target_file}::outer.inner"])

    assert exit_code == EXIT_CLI_USAGE_ERROR
    assert "nested functions are not supported" in stderr.getvalue()


def test_complexity_non_existent_file(tmp_path: Path) -> None:
    """Verify non-existent target file raises EXIT_TARGET_IO_ERROR (3)."""
    target_file = tmp_path / "does_not_exist.py"

    stderr = io.StringIO()
    with redirect_stderr(stderr):
        exit_code = main(["complexity", str(target_file)])

    assert exit_code == EXIT_TARGET_IO_ERROR


def test_complexity_unsupported_language(tmp_path: Path) -> None:
    """Verify non-Python file raises EXIT_ABSTENTION (6)."""
    target_file = tmp_path / "script.js"
    target_file.write_text("console.log(42);\n", encoding="utf-8")

    stderr = io.StringIO()
    with redirect_stderr(stderr):
        exit_code = main(["complexity", str(target_file)])

    assert exit_code == EXIT_ABSTENTION


def test_complexity_non_execution_invariant(tmp_path: Path) -> None:
    """Verify static analysis NEVER imports or executes target code."""
    canary = tmp_path / "canary.txt"
    target_file = tmp_path / "dangerous.py"
    target_file.write_text(
        f"from pathlib import Path\n"
        f"Path({str(canary)!r}).write_text('EXECUTED')\n"
        f"raise RuntimeError('Target must not be executed!')\n"
        f"def safe_func(): return 1\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        exit_code = main(["complexity", f"{target_file}::safe_func"])

    assert exit_code == EXIT_SUCCESS
    assert not canary.exists()


def test_complexity_subprocess_execution(tmp_path: Path) -> None:
    """Verify 'python -m localdev.cli complexity' executes cleanly via subprocess."""
    target_file = tmp_path / "cli_test.py"
    target_file.write_text(
        "def fib(n: int) -> int:\n"
        "    if n <= 1: return n\n"
        "    return fib(n - 1) + fib(n - 2)\n",
        encoding="utf-8",
    )

    cmd = [
        sys.executable,
        "-m",
        "localdev.cli",
        "complexity",
        f"{target_file}::fib",
        "--json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert proc.returncode == EXIT_SUCCESS

    payload = json.loads(proc.stdout)
    assert payload["success"] is True
    assert payload["data"]["time_complexity"] == "O(2^n)"
    assert payload["data"]["auxiliary_space"] == "O(n)"
