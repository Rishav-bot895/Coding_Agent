"""Pytest configuration and common fixtures for localdev test suite.

Registers custom markers for native Windows testing and integration suites,
and provides isolated filesystem fixtures.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Generator

import pytest

# Ensure localdev package root is in sys.path for direct test runs
PACKAGE_ROOT = Path(__file__).parent.parent
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers",
        "windows: marks tests that require native Windows 11 x64 APIs (Job Objects, ReplaceFileW, etc.)",
    )
    config.addinivalue_line(
        "markers",
        "integration: marks integration tests exercising multiple subsystems end-to-end",
    )


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Generator[Path, None, None]:
    """Provide an isolated temporary directory for test fixtures."""
    yield tmp_path


@pytest.fixture
def sample_clean_py(temp_workspace: Path) -> Path:
    """Provide a clean, valid Python source target."""
    target = temp_workspace / "clean_target.py"
    target.write_text(
        '"""Clean target module."""\n\n'
        'def add(a: int, b: int) -> int:\n'
        '    """Add two numbers."""\n'
        '    return a + b\n\n'
        'if __name__ == "__main__":\n'
        '    print(add(2, 3))\n',
        encoding="utf-8",
    )
    return target


@pytest.fixture
def sample_syntax_error_py(temp_workspace: Path) -> Path:
    """Provide a Python file with a deliberate syntax error."""
    target = temp_workspace / "syntax_error_target.py"
    target.write_text(
        'def broken_function(\n'
        '    print("missing closing paren and colon"\n',
        encoding="utf-8",
    )
    return target


@pytest.fixture
def sample_runtime_error_py(temp_workspace: Path) -> Path:
    """Provide a Python file that raises a runtime exception."""
    target = temp_workspace / "runtime_error_target.py"
    target.write_text(
        'def divide(a: int, b: int) -> float:\n'
        '    return a / b\n\n'
        'if __name__ == "__main__":\n'
        '    divide(10, 0)\n',
        encoding="utf-8",
    )
    return target

