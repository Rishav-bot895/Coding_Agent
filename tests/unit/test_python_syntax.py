"""Unit tests for Python syntax validation and error extraction.

Verifies:
1. Pure AST validation with zero execution or import side-effects.
2. Bytecode suppression (sys.dont_write_bytecode = True, zero .pyc / __pycache__).
3. Detection of SyntaxError and IndentationError with exact 1-based coordinates.
4. Normalization into DiagnosticRecord and SyntaxErrorDetail.
5. Handling of PEP 263 encodings, UTF-8 BOM, and CRLF line endings.
6. Integration with PythonAdapter.check_syntax.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from localdev.agent.permissions import validate_target
from localdev.languages.python.adapter import PythonAdapter
from localdev.languages.python.syntax import (
    SyntaxErrorDetail,
    compile_ast_only,
    decode_source,
    parse_syntax_error,
    validate_python_syntax,
)
from localdev.schemas import SeverityEnum, TargetRecord

FIXTURES_DIR = Path(__file__).parent.parent / "bug_samples" / "syntax"


def _make_dummy_target(target_path: Path) -> TargetRecord:
    content = target_path.read_text("utf-8", errors="ignore") if target_path.is_file() else ""
    return TargetRecord(
        path=target_path.name,
        absolute_path=str(target_path.resolve()),
        file_size_bytes=target_path.stat().st_size if target_path.is_file() else 0,
        sha256="0" * 64,
        encoding="utf-8",
        has_bom=False,
        newline_style="\r\n" if "\r\n" in content else "\n",
        has_trailing_newline=content.endswith(("\n", "\r")),
        is_read_only=False,
        is_reparse_point=False,
    )


def test_valid_syntax_ast_and_diags() -> None:
    sample_file = FIXTURES_DIR / "valid_syntax.py.sample"
    content = sample_file.read_text(encoding="utf-8")

    tree, diags, detail = compile_ast_only(content, filename="valid.py")

    assert tree is not None
    assert isinstance(tree, ast.Module)
    assert len(diags) == 0
    assert detail is None

    # Check extracted AST contains the expected function definition
    func_names = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)]
    assert func_names == ["add"]


def test_syntax_error_missing_colon() -> None:
    sample_file = FIXTURES_DIR / "syntax_missing_colon.py.sample"
    content = sample_file.read_text(encoding="utf-8")

    tree, diags, detail = compile_ast_only(content, filename="broken_colon.py")

    assert tree is None
    assert len(diags) == 1
    assert detail is not None

    diag = diags[0]
    assert diag.source == "python_syntax"
    assert diag.code == "SyntaxError"
    assert diag.severity == SeverityEnum.ERROR
    assert diag.start_line == 1
    assert diag.end_line == 1
    assert diag.start_col <= diag.end_col
    assert not diag.fix_available
    assert "expected ':'" in diag.message.lower() or ":" in diag.message

    assert detail.error_type == "SyntaxError"
    assert detail.line == 1
    assert detail.source_line == "def calculate(x, y)"
    assert detail.filename == "broken_colon.py"


def test_syntax_error_invalid_token() -> None:
    sample_file = FIXTURES_DIR / "syntax_invalid_token.py.sample"
    content = sample_file.read_text(encoding="utf-8")

    tree, diags, detail = compile_ast_only(content, filename="invalid_token.py")

    assert tree is None
    assert len(diags) == 1
    assert detail is not None
    assert diags[0].code == "SyntaxError"
    assert diags[0].start_line == 1
    assert detail.source_line == "x = 10 + * 2"


def test_syntax_error_unclosed_paren() -> None:
    sample_file = FIXTURES_DIR / "syntax_unclosed_paren.py.sample"
    content = sample_file.read_text(encoding="utf-8")

    tree, diags, detail = compile_ast_only(content, filename="unclosed.py")

    assert tree is None
    assert len(diags) == 1
    assert detail is not None
    assert diags[0].code == "SyntaxError"
    assert "was never closed" in diags[0].message.lower() or "unclosed" in diags[0].message.lower()


def test_indentation_error_unexpected() -> None:
    sample_file = FIXTURES_DIR / "indent_unexpected.py.sample"
    content = sample_file.read_text(encoding="utf-8")

    tree, diags, detail = compile_ast_only(content, filename="indent_err.py")

    assert tree is None
    assert len(diags) == 1
    assert detail is not None

    diag = diags[0]
    assert diag.code == "IndentationError"
    assert diag.start_line == 3
    assert "unexpected indent" in diag.message.lower()
    assert detail.error_type == "IndentationError"
    assert detail.line == 3
    assert detail.source_line.strip() == "y = 2"


def test_indentation_error_unindent() -> None:
    sample_file = FIXTURES_DIR / "indent_unindent.py.sample"
    content = sample_file.read_text(encoding="utf-8")

    tree, diags, detail = compile_ast_only(content, filename="unindent_err.py")

    assert tree is None
    assert len(diags) == 1
    assert detail is not None

    diag = diags[0]
    assert diag.code == "IndentationError"
    assert diag.start_line == 4
    assert "unindent does not match" in diag.message.lower()


def test_zero_runtime_side_effects() -> None:
    """Verify that dangerous code is compiled to AST without execution."""
    sample_file = FIXTURES_DIR / "side_effects_dangerous.py.sample"
    content = sample_file.read_text(encoding="utf-8")

    # If this executed, RuntimeError('DANGEROUS_SIDE_EFFECT_EXECUTED') would be raised.
    tree, diags, detail = compile_ast_only(content, filename="dangerous.py")

    assert tree is not None
    assert len(diags) == 0
    assert detail is None

    # Test with other lethal calls: sys.exit, os._exit, infinite recursion
    dangerous_code = """
import os
import sys

def will_never_run():
    os._exit(99)
    sys.exit(1)

raise SystemExit(123)
"""
    tree2, diags2, detail2 = compile_ast_only(dangerous_code, filename="lethal.py")
    assert tree2 is not None
    assert len(diags2) == 0
    assert detail2 is None


def test_sys_dont_write_bytecode_and_zero_filesystem_artifacts(tmp_path: Path) -> None:
    """Ensure sys.dont_write_bytecode is enforced and no .pyc or __pycache__ are written."""
    sys.dont_write_bytecode = False

    target_py = tmp_path / "sample.py"
    target_py.write_text("def test():\n    return 42\n", encoding="utf-8")

    target = validate_target(str(target_py))
    diags = validate_python_syntax(target)

    assert len(diags) == 0
    assert sys.dont_write_bytecode is True

    # Assert no .pyc files anywhere in target directory
    pyc_files = list(tmp_path.rglob("*.pyc"))
    assert len(pyc_files) == 0

    # Assert no __pycache__ directories anywhere in target directory
    pycache_dirs = list(tmp_path.rglob("__pycache__"))
    assert len(pycache_dirs) == 0


def test_pep263_latin1_encoding(tmp_path: Path) -> None:
    """Verify PEP 263 latin-1 encoding cookie is respected when reading bytes."""
    sample_file = FIXTURES_DIR / "pep263_latin1.py.sample"
    raw_bytes = sample_file.read_bytes()

    tree, diags, detail = compile_ast_only(raw_bytes, filename="latin1.py")
    assert tree is not None
    assert len(diags) == 0
    assert detail is None

    target_file = tmp_path / "latin1.py"
    target_file.write_bytes(raw_bytes)
    target = validate_target(str(target_file))

    target_diags = validate_python_syntax(target)
    assert len(target_diags) == 0


def test_pep263_unknown_encoding() -> None:
    """Verify unknown encoding in PEP 263 header returns normalized SyntaxError."""
    sample_file = FIXTURES_DIR / "pep263_unknown.py.sample"
    raw_bytes = sample_file.read_bytes()

    tree, diags, detail = compile_ast_only(raw_bytes, filename="unknown_enc.py")
    assert tree is None
    assert len(diags) == 1
    assert detail is not None
    assert diags[0].code == "SyntaxError"
    assert "unknown_encoding_999" in diags[0].message


def test_crlf_newlines(tmp_path: Path) -> None:
    """Verify files with CRLF newlines validate cleanly."""
    sample_file = FIXTURES_DIR / "crlf_newlines.py.sample"
    raw_bytes = sample_file.read_bytes()
    assert b"\r\n" in raw_bytes

    target_file = tmp_path / "crlf.py"
    target_file.write_bytes(raw_bytes)
    target = validate_target(str(target_file))

    diags = validate_python_syntax(target)
    assert len(diags) == 0


def test_utf8_bom(tmp_path: Path) -> None:
    """Verify files with UTF-8 BOM validate cleanly."""
    sample_file = FIXTURES_DIR / "utf8_bom.py.sample"
    raw_bytes = sample_file.read_bytes()

    target_file = tmp_path / "bom.py"
    target_file.write_bytes(raw_bytes)
    target = validate_target(str(target_file))
    assert target.has_bom is True

    diags = validate_python_syntax(target)
    assert len(diags) == 0


def test_adapter_check_syntax_integration(tmp_path: Path) -> None:
    """Verify PythonAdapter.check_syntax delegates cleanly."""
    adapter = PythonAdapter()

    valid_file = tmp_path / "valid.py"
    valid_file.write_text("x = 10\n", encoding="utf-8")
    valid_target = validate_target(str(valid_file))

    assert len(adapter.check_syntax(valid_target)) == 0

    broken_file = tmp_path / "broken.py"
    broken_file.write_text("def foo(\n", encoding="utf-8")
    broken_target = _make_dummy_target(broken_file)

    diags = adapter.check_syntax(broken_target)
    assert len(diags) == 1
    assert diags[0].code == "SyntaxError"

    # Test override with source_text parameter
    diags_override = adapter.check_syntax(valid_target, source_text="def broken(\n")
    assert len(diags_override) == 1
    assert diags_override[0].code == "SyntaxError"


def test_decode_source_helper() -> None:
    """Verify decode_source handles UTF-8 BOM, declared encodings, and fallbacks."""
    # Standard UTF-8
    text, enc, has_bom = decode_source(b"hello = 1\n")
    assert text == "hello = 1\n"
    assert enc.lower() == "utf-8"
    assert not has_bom

    # BOM UTF-8
    bom_bytes = b"\xef\xbb\xbfx = 1\n"
    text, enc, has_bom = decode_source(bom_bytes)
    assert text == "x = 1\n"
    assert enc.lower() == "utf-8"
    assert has_bom

    # Corrupt / invalid bytes fallback
    corrupt_bytes = b"\xff\xfe\x00\x00corrupt"
    text, enc, _ = decode_source(corrupt_bytes)
    assert isinstance(text, str)


def test_null_bytes_error() -> None:
    """Verify null bytes in source string produce a clean DiagnosticRecord."""
    tree, diags, detail = compile_ast_only("def foo():\n    x = \x00\n", filename="null.py")
    assert tree is None
    assert len(diags) == 1
    assert diags[0].code in ("SyntaxError", "ValueError")
    assert "null" in diags[0].message.lower()
    assert detail is not None
    assert detail.error_type in ("SyntaxError", "ValueError")


def test_missing_or_unreadable_file_diagnostics(tmp_path: Path) -> None:
    """Verify missing file returns empty diagnostics and IO error is captured."""
    missing = tmp_path / "missing.py"
    target = _make_dummy_target(missing)
    diags = validate_python_syntax(target)
    assert diags == []


def test_parse_syntax_error_coordinate_clamping() -> None:
    """Verify that None / 0 coordinate values are clamped to 1 and valid ranges."""
    err = SyntaxError("invalid syntax")
    err.lineno = None
    err.offset = None
    err.end_lineno = None
    err.end_offset = None
    err.text = None

    diag, detail = parse_syntax_error(err, filename="none_coords.py")
    assert diag.start_line == 1
    assert diag.start_col == 1
    assert diag.end_line == 1
    assert diag.end_col >= diag.start_col
    assert detail.line == 1
    assert detail.column == 1
    assert detail.source_line == ""


def test_syntax_error_detail_immutability() -> None:
    """Verify SyntaxErrorDetail is a frozen dataclass."""
    detail = SyntaxErrorDetail(
        error_type="SyntaxError",
        message="msg",
        line=1,
        column=1,
        end_line=1,
        end_column=1,
        source_line="x = 1",
        filename="test.py",
    )

    with pytest.raises(FrozenInstanceError):
        detail.line = 2  # type: ignore[misc]


def test_empty_source_ast() -> None:
    """Verify empty source string or bytes compiles cleanly with zero diagnostics."""
    tree_str, diags_str, detail_str = compile_ast_only("", filename="empty.py")
    assert tree_str is not None
    assert len(diags_str) == 0
    assert detail_str is None

    tree_bytes, diags_bytes, detail_bytes = compile_ast_only(b"", filename="empty.py")
    assert tree_bytes is not None
    assert len(diags_bytes) == 0
    assert detail_bytes is None


def test_validate_python_syntax_io_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify OSError when reading target file produces an IOError DiagnosticRecord."""
    target_py = tmp_path / "locked.py"
    target_py.write_text("x = 1\n", encoding="utf-8")
    target = validate_target(str(target_py))

    def mock_read_bytes(self: Path) -> bytes:
        raise OSError("Access is denied")

    monkeypatch.setattr(Path, "read_bytes", mock_read_bytes)
    diags = validate_python_syntax(target)
    assert len(diags) == 1
    assert diags[0].code == "IOError"
    assert "Access is denied" in diags[0].message
    assert diags[0].severity == SeverityEnum.ERROR


def test_parse_syntax_error_end_col_clamping() -> None:
    """Verify end_col is clamped when end_offset is less than offset on same line."""
    err = SyntaxError("invalid syntax")
    err.lineno = 5
    err.offset = 10
    err.end_lineno = 5
    err.end_offset = 4  # Less than start_col

    diag, detail = parse_syntax_error(err, filename="clamp.py")
    assert diag.start_line == 5
    assert diag.start_col == 10
    assert diag.end_line == 5
    assert diag.end_col >= 10
    assert detail.end_column >= 10


def test_adapter_capabilities() -> None:
    """Verify PythonAdapter declares syntax check capabilities."""
    adapter = PythonAdapter()
    assert adapter.capabilities.supports_syntax_check is True

