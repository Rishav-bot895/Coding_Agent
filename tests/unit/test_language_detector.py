"""Unit tests for layered, non-executing target language detection.

Verifies:
1. Valid Python files classified as CERTAIN with extension_match and ast_parse_success.
2. Syntax-broken .py files classified as PROBABLE with ast_syntax_error.
3. Extensionless scripts with Python shebang classified as CERTAIN or PROBABLE.
4. Misleading extensions:
   - Python code with non-Python extension and no shebang classified as UNSUPPORTED.
   - Non-Python extension with valid Python shebang classified as CERTAIN.
5. Conflicting shebangs (.py extension with non-Python shebang) classified as UNSUPPORTED.
6. Binary files with null bytes classified as UNSUPPORTED.
7. HTML and JavaScript files classified as UNSUPPORTED.
8. Empty files:
   - Empty .py file classified as CERTAIN.
   - Empty extensionless file classified as UNSUPPORTED.
9. Shebang parsing variants (env, absolute paths, Windows executables, flags).
10. Strict invariant: Language detection NEVER spawns a subprocess.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from localdev.agent.orchestrator import Orchestrator
from localdev.agent.permissions import validate_target
from localdev.errors import TargetValidationError
from localdev.languages.base import get_default_registry
from localdev.languages.detector import (
    detect_target_language,
    is_binary_target,
    parse_shebang,
)
from localdev.languages.python.adapter import PythonAdapter
from localdev.schemas import DetectionConfidence, TargetRecord

SAMPLES_DIR = Path(__file__).parent.parent / "boundary_samples" / "languages"


@pytest.fixture(autouse=True)
def guard_no_subprocesses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strictly assert that no subprocess is spawned during any language detection test."""

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Language detection must NEVER spawn a subprocess!")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "call", forbidden)
    monkeypatch.setattr(subprocess, "check_call", forbidden)
    monkeypatch.setattr(subprocess, "check_output", forbidden)


# =============================================================================
# Helper Function
# =============================================================================


def _get_target(filename: str) -> TargetRecord:
    path = SAMPLES_DIR / filename
    try:
        return validate_target(path)
    except TargetValidationError:
        raw = path.read_bytes()
        return TargetRecord(
            path=filename,
            absolute_path=str(path.resolve()),
            file_size_bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            encoding="utf-8",
            has_bom=False,
            newline_style="\n",
            has_trailing_newline=False,
            is_read_only=False,
            is_reparse_point=False,
        )


# =============================================================================
# Tests: Valid and Syntax-Broken Python Targets
# =============================================================================


def test_detect_valid_python_file() -> None:
    """Valid Python file with .py extension is detected as CERTAIN."""
    target = _get_target("valid.py")
    adapter, result = detect_target_language(target)

    assert adapter is not None
    assert adapter.name == "python"
    assert result.language == "python"
    assert result.confidence == DetectionConfidence.CERTAIN
    assert any("extension_match" in r for r in result.reasons)
    assert "ast_parse_success" in result.reasons


def test_detect_syntax_broken_python_file(tmp_path: Path) -> None:
    """Syntax-broken Python file with .py extension is detected as PROBABLE."""
    broken_file = tmp_path / "syntax_broken.py"
    broken_file.write_text("def calculate(a, b\n    return a + b\n", encoding="utf-8")
    target = validate_target(broken_file)
    adapter, result = detect_target_language(target)

    assert adapter is not None
    assert adapter.name == "python"
    assert result.language == "python"
    assert result.confidence == DetectionConfidence.PROBABLE
    assert any("extension_match" in r for r in result.reasons)
    assert any("ast_syntax_error" in r for r in result.reasons)


# =============================================================================
# Tests: Extensionless Scripts with Shebangs
# =============================================================================


def test_detect_shebang_script() -> None:
    """Extensionless script with python shebang is detected as CERTAIN."""
    target = _get_target("shebang_script")
    adapter, result = detect_target_language(target)

    assert adapter is not None
    assert adapter.name == "python"
    assert result.language == "python"
    assert result.confidence == DetectionConfidence.CERTAIN
    assert result.has_shebang is True
    assert any("shebang_match" in r for r in result.reasons)
    assert "ast_parse_success" in result.reasons


def test_detect_shebang_broken() -> None:
    """Extensionless script with python shebang and syntax error is detected as PROBABLE."""
    target = _get_target("shebang_broken")
    adapter, result = detect_target_language(target)

    assert adapter is not None
    assert adapter.name == "python"
    assert result.language == "python"
    assert result.confidence == DetectionConfidence.PROBABLE
    assert result.has_shebang is True
    assert any("shebang_match" in r for r in result.reasons)
    assert any("ast_syntax_error" in r for r in result.reasons)


# =============================================================================
# Tests: Misleading Extensions and Conflicts
# =============================================================================


def test_detect_misleading_extension_without_shebang() -> None:
    """Python code with .txt extension and NO shebang is safely classified as UNSUPPORTED."""
    target = _get_target("misleading_ext.txt")
    adapter, result = detect_target_language(target)

    assert adapter is None
    assert result.language == "unsupported"
    assert result.confidence == DetectionConfidence.UNSUPPORTED
    assert any("no_python_extension_or_shebang" in r for r in result.reasons)


def test_detect_misleading_extension_with_shebang() -> None:
    """File with .txt extension but valid Python shebang is detected as CERTAIN."""
    target = _get_target("misleading_with_shebang.txt")
    adapter, result = detect_target_language(target)

    assert adapter is not None
    assert adapter.name == "python"
    assert result.language == "python"
    assert result.confidence == DetectionConfidence.CERTAIN
    assert result.has_shebang is True
    assert any("shebang_match" in r for r in result.reasons)


def test_detect_conflicting_shebang() -> None:
    """.py extension with non-Python shebang (e.g. bash) is detected as conflict and UNSUPPORTED."""
    target = _get_target("conflicting_shebang.py")
    adapter, result = detect_target_language(target)

    assert adapter is None
    assert result.language == "unsupported"
    assert result.confidence == DetectionConfidence.UNSUPPORTED
    assert any("conflicting_shebang_detected" in r for r in result.reasons)


# =============================================================================
# Tests: Binary, HTML, and JS Files
# =============================================================================


def test_detect_binary_file_with_py_extension(tmp_path: Path) -> None:
    """Binary file with .py extension is safely detected as UNSUPPORTED."""
    bin_file = tmp_path / "binary.py"
    bin_file.write_bytes(b"# header\n# line 2\n\x00\x01\x02\x03\x04\x05")
    target = validate_target(bin_file)
    assert is_binary_target(bin_file) is True

    adapter, result = detect_target_language(target)
    assert adapter is None
    assert result.language == "unsupported"
    assert result.confidence == DetectionConfidence.UNSUPPORTED
    assert any("binary_file_detected" in r for r in result.reasons)


def test_detect_binary_file_bin() -> None:
    """Binary file with .bin extension is safely detected as UNSUPPORTED."""
    target = _get_target("binary.bin")
    adapter, result = detect_target_language(target)

    assert adapter is None
    assert result.language == "unsupported"
    assert result.confidence == DetectionConfidence.UNSUPPORTED


def test_detect_html_file() -> None:
    """HTML document is classified as UNSUPPORTED."""
    target = _get_target("page.html")
    adapter, result = detect_target_language(target)

    assert adapter is None
    assert result.language == "unsupported"
    assert result.confidence == DetectionConfidence.UNSUPPORTED
    assert any(
        "non_python_extension" in r or "markup" in r for r in result.reasons
    )


def test_detect_js_file() -> None:
    """JavaScript source file is classified as UNSUPPORTED."""
    target = _get_target("script.js")
    adapter, result = detect_target_language(target)

    assert adapter is None
    assert result.language == "unsupported"
    assert result.confidence == DetectionConfidence.UNSUPPORTED
    assert any("non_python_extension" in r for r in result.reasons)


# =============================================================================
# Tests: Empty Files
# =============================================================================


def test_detect_empty_py_file() -> None:
    """Empty .py file is a valid empty Python module, classified as CERTAIN."""
    target = _get_target("empty.py")
    adapter, result = detect_target_language(target)

    assert adapter is not None
    assert adapter.name == "python"
    assert result.language == "python"
    assert result.confidence == DetectionConfidence.CERTAIN
    assert "empty_python_file" in result.reasons


def test_detect_empty_no_ext_file() -> None:
    """Empty extensionless file has no signals and is classified as UNSUPPORTED."""
    target = _get_target("empty_no_ext")
    adapter, result = detect_target_language(target)

    assert adapter is None
    assert result.language == "unsupported"
    assert result.confidence == DetectionConfidence.UNSUPPORTED
    assert "empty_file_without_python_extension" in result.reasons


# =============================================================================
# Tests: Shebang Parsing Logic
# =============================================================================


@pytest.mark.parametrize(
    ("line", "expected_has", "expected_py", "expected_name"),
    [
        ("#!/usr/bin/env python3", True, True, "python3"),
        ("#!/usr/bin/env python", True, True, "python"),
        ("#!/usr/bin/env -S python3 -u", True, True, "python3"),
        ("#!/usr/bin/python", True, True, "python"),
        ("#!/usr/local/bin/python3.12", True, True, "python3.12"),
        ("#!python", True, True, "python"),
        ("#!python.exe", True, True, "python"),
        (r"#!C:\Python312\python.exe", True, True, "python"),
        ("#!/bin/bash", True, False, "bash"),
        ("#!/bin/sh", True, False, "sh"),
        ("#!/usr/bin/env node", True, False, "node"),
        ("#!/usr/bin/ruby", True, False, "ruby"),
        ("#!/usr/bin/perl", True, False, "perl"),
        ("# not a shebang", False, False, None),
        ("// not a shebang", False, False, None),
        ("", False, False, None),
    ],
)
def test_parse_shebang(
    line: str,
    expected_has: bool,
    expected_py: bool,
    expected_name: str | None,
) -> None:
    """Verify various shebang formats and non-shebang lines."""
    has_shebang, _, is_py, name = parse_shebang(line)
    assert has_shebang == expected_has
    assert is_py == expected_py
    assert name == expected_name


# =============================================================================
# Tests: Adapter and Orchestrator Integration
# =============================================================================


def test_python_adapter_detect_confidence_integration() -> None:
    """Verify PythonAdapter.detect_confidence produces identical results."""
    adapter = PythonAdapter()
    target_valid = _get_target("valid.py")
    res_valid = adapter.detect_confidence(target_valid)
    assert res_valid.confidence == DetectionConfidence.CERTAIN

    target_other = _get_target("page.html")
    res_other = adapter.detect_confidence(target_other)
    assert res_other.confidence == DetectionConfidence.UNSUPPORTED


def test_orchestrator_detect_integration() -> None:
    """Verify Orchestrator.detect produces identical results via registry."""
    orchestrator = Orchestrator(registry=get_default_registry())

    target_valid = _get_target("valid.py")
    res_valid = orchestrator.detect(target_valid)
    assert res_valid.confidence == DetectionConfidence.CERTAIN
    assert res_valid.language == "python"

    target_unsupp = _get_target("script.js")
    res_unsupp = orchestrator.detect(target_unsupp)
    assert res_unsupp.confidence == DetectionConfidence.UNSUPPORTED

