"""Layered, non-executing target language detection for localdev.

Classifies target files into CERTAIN, PROBABLE, or UNSUPPORTED Python using
multi-layered static signals:
1. Binary / null-byte safety check.
2. File extension inspection (.py, .pyw).
3. Standard shebang inspection (e.g. #!/usr/bin/env python3, #!python).
4. Explicit conflict detection (e.g. .py extension with non-Python shebang).
5. Non-executing AST parse verification (ast.parse in exec mode).

Never imports target modules or spawns external subprocesses.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import TYPE_CHECKING

from localdev.schemas import (
    DetectionConfidence,
    DetectionResult,
    TargetRecord,
)

if TYPE_CHECKING:
    from localdev.languages.base import AdapterRegistry, LanguageAdapter

# Known non-Python file extensions that should be rejected without Python shebang
KNOWN_NON_PYTHON_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".c",
        ".cpp",
        ".cs",
        ".css",
        ".go",
        ".html",
        ".htm",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".md",
        ".php",
        ".rb",
        ".rs",
        ".sh",
        ".sql",
        ".ts",
        ".tsx",
        ".xml",
        ".yaml",
        ".yml",
    }
)

SHEBANG_PATTERN: re.Pattern[str] = re.compile(
    r"^#!\s*(?:/usr/bin/env\s+(?:-S\s+)?)?([^\s]+)"
)


def parse_shebang(first_line: str) -> tuple[bool, str | None, bool, str | None]:
    """Inspect first line for a Unix-style shebang.

    Returns:
        (has_shebang, raw_shebang_line, is_python, interpreter_name)
    """
    line = first_line.lstrip("\ufeff").strip()
    if not line.startswith("#!"):
        return False, None, False, None

    match = SHEBANG_PATTERN.match(line)
    if not match:
        return True, line, False, None

    interpreter_path = match.group(1)
    interpreter_name = Path(interpreter_path).name.lower().removesuffix(".exe")

    is_python = interpreter_name.startswith("python")
    return True, line, is_python, interpreter_name


def is_binary_target(target_path: Path) -> bool:
    """Check if target file contains null bytes indicating binary content."""
    try:
        with open(target_path, "rb") as f:
            chunk = f.read(8192)
            return b"\x00" in chunk
    except OSError:
        return False


def detect_target_language(
    target: TargetRecord,
    source_text: str | None = None,
    registry: AdapterRegistry | None = None,
) -> tuple[LanguageAdapter | None, DetectionResult]:
    """Detect whether target file is supported Python using layered non-executing signals.

    Evaluation layers:
    1. Binary / null-byte safety check: immediately marks binary files as UNSUPPORTED.
    2. Shebang and extension extraction: identifies primary indicators and conflicts.
    3. Conflict detection: .py extension combined with non-Python shebang is marked UNSUPPORTED.
    4. Non-executing AST parse check: verifies syntax without code execution.
    5. Scoring:
       - CERTAIN: .py/.pyw with clean AST, or Python shebang with clean AST.
       - PROBABLE: .py/.pyw with syntax error, or Python shebang with syntax error.
       - UNSUPPORTED: binary, non-Python extension without Python shebang, conflicting shebang,
         or non-Python source text.

    Returns:
        (matching_adapter_or_none, DetectionResult)
    """
    from localdev.languages.base import get_default_registry

    active_registry = registry or get_default_registry()
    python_adapter = active_registry.get("python")

    target_path = Path(target.absolute_path)

    # Layer 1: Binary safety check
    if is_binary_target(target_path):
        return None, DetectionResult(
            language="unsupported",
            confidence=DetectionConfidence.UNSUPPORTED,
            reasons=["binary_file_detected: null bytes present"],
            matched_extension=Path(target.path).suffix or None,
            has_shebang=False,
        )

    # Read source text if not already supplied
    source: str = ""
    if source_text is not None:
        source = source_text
    elif target_path.is_file():
        try:
            source = target_path.read_text(encoding=target.encoding)
        except (OSError, UnicodeDecodeError):
            try:
                source = target_path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                source = ""

    # Check for binary characters in supplied source_text
    if "\x00" in source:
        return None, DetectionResult(
            language="unsupported",
            confidence=DetectionConfidence.UNSUPPORTED,
            reasons=["binary_file_detected: null bytes in source"],
            matched_extension=Path(target.path).suffix or None,
            has_shebang=False,
        )

    # Layer 2: Extension and shebang inspection
    path_suffix = Path(target.path).suffix.lower()
    has_py_ext = path_suffix in (".py", ".pyw")

    first_line = source.splitlines()[0] if source.splitlines() else ""
    has_shebang, shebang_line, shebang_is_python, interpreter_name = parse_shebang(first_line)

    # Layer 3: Conflict detection
    if has_shebang and not shebang_is_python and has_py_ext:
        return None, DetectionResult(
            language="unsupported",
            confidence=DetectionConfidence.UNSUPPORTED,
            reasons=[
                f"conflicting_shebang_detected: .py extension with non-Python interpreter '{interpreter_name}'"
            ],
            matched_extension=path_suffix,
            has_shebang=True,
        )

    # Known non-Python extension without Python shebang -> reject early
    if path_suffix in KNOWN_NON_PYTHON_EXTENSIONS and not shebang_is_python:
        return None, DetectionResult(
            language="unsupported",
            confidence=DetectionConfidence.UNSUPPORTED,
            reasons=[f"non_python_extension_without_python_shebang: {path_suffix}"],
            matched_extension=path_suffix,
            has_shebang=has_shebang,
        )

    # HTML document check (<html, <!DOCTYPE html)
    stripped_source = source.lstrip().lower()
    if stripped_source.startswith(("<!doctype html", "<html", "<?xml", "<?php")):
        return None, DetectionResult(
            language="unsupported",
            confidence=DetectionConfidence.UNSUPPORTED,
            reasons=["markup_or_non_python_header_detected"],
            matched_extension=path_suffix or None,
            has_shebang=has_shebang,
        )

    # Layer 4: Empty file handling
    is_empty_or_whitespace = not source.strip()
    if is_empty_or_whitespace:
        if has_py_ext:
            return python_adapter, DetectionResult(
                language="python",
                confidence=DetectionConfidence.CERTAIN,
                reasons=["extension_match", "empty_python_file", "ast_parse_success"],
                matched_extension=path_suffix,
                has_shebang=False,
            )
        return None, DetectionResult(
            language="unsupported",
            confidence=DetectionConfidence.UNSUPPORTED,
            reasons=["empty_file_without_python_extension"],
            matched_extension=path_suffix or None,
            has_shebang=False,
        )

    # Layer 5: AST parse validation (pure static, no code execution)
    ast_parses = False
    ast_error: str | None = None
    try:
        ast.parse(source, filename=target.path, mode="exec")
        ast_parses = True
    except (SyntaxError, IndentationError, ValueError) as err:
        ast_error = str(err)

    # Layer 6: Confidence classification
    reasons: list[str] = []

    if has_py_ext:
        reasons.append(f"extension_match: {path_suffix}")
        if ast_parses:
            reasons.append("ast_parse_success")
            return python_adapter, DetectionResult(
                language="python",
                confidence=DetectionConfidence.CERTAIN,
                reasons=reasons,
                matched_extension=path_suffix,
                has_shebang=has_shebang,
            )
        reasons.append(f"ast_syntax_error: {ast_error}")
        return python_adapter, DetectionResult(
            language="python",
            confidence=DetectionConfidence.PROBABLE,
            reasons=reasons,
            matched_extension=path_suffix,
            has_shebang=has_shebang,
        )

    if shebang_is_python:
        reasons.append(f"shebang_match: {shebang_line}")
        if ast_parses:
            reasons.append("ast_parse_success")
            return python_adapter, DetectionResult(
                language="python",
                confidence=DetectionConfidence.CERTAIN,
                reasons=reasons,
                matched_extension=path_suffix or None,
                has_shebang=True,
            )
        reasons.append(f"ast_syntax_error: {ast_error}")
        return python_adapter, DetectionResult(
            language="python",
            confidence=DetectionConfidence.PROBABLE,
            reasons=reasons,
            matched_extension=path_suffix or None,
            has_shebang=True,
        )

    # Files with no Python extension and no Python shebang are unsupported
    reasons.append("no_python_extension_or_shebang")
    return None, DetectionResult(
        language="unsupported",
        confidence=DetectionConfidence.UNSUPPORTED,
        reasons=reasons,
        matched_extension=path_suffix or None,
        has_shebang=has_shebang,
    )
