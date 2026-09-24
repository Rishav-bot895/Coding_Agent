"""Python syntax decoding and validation without execution side-effects.

Decodes source files according to PEP 263 encoding declarations (defaulting to UTF-8),
suppresses bytecode generation, and validates syntax strictly via:
    compile(source, filename, mode="exec", flags=ast.PyCF_ONLY_AST)

Guarantees:
- Zero code execution or module import side effects.
- Zero bytecode emission (sys.dont_write_bytecode = True).
- Exact 1-based start and end line/column locations for SyntaxError and IndentationError.
- Normalization into DiagnosticRecord schemas.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from localdev.agent.permissions import detect_encoding_and_bom
from localdev.errors import TargetValidationError
from localdev.schemas import DiagnosticRecord, SeverityEnum

if TYPE_CHECKING:
    from localdev.schemas import TargetRecord


@dataclass(frozen=True)
class SyntaxErrorDetail:
    """Detailed error attributes extracted from a Python SyntaxError or IndentationError."""

    error_type: str
    message: str
    line: int
    column: int
    end_line: int
    end_column: int
    source_line: str
    filename: str


def parse_syntax_error(
    err: SyntaxError,
    filename: str = "<string>",
) -> tuple[DiagnosticRecord, SyntaxErrorDetail]:
    """Extract normalized DiagnosticRecord and detailed metadata from a SyntaxError."""
    line = max(1, err.lineno or 1)
    col = max(1, err.offset or 1)
    end_line = max(line, err.end_lineno or line)
    end_col = err.end_offset or col
    if end_line == line:
        end_col = max(col, end_col)

    msg = err.msg or "Syntax error"
    err_type = type(err).__name__
    source_line = (err.text or "").rstrip("\r\n")
    resolved_filename = err.filename or filename

    diag = DiagnosticRecord(
        source="python_syntax",
        code=err_type,
        message=msg,
        severity=SeverityEnum.ERROR,
        start_line=line,
        start_col=col,
        end_line=end_line,
        end_col=end_col,
        fix_available=False,
    )

    detail = SyntaxErrorDetail(
        error_type=err_type,
        message=msg,
        line=line,
        column=col,
        end_line=end_line,
        end_column=end_col,
        source_line=source_line,
        filename=resolved_filename,
    )

    return diag, detail


def decode_source(raw_bytes: bytes, declared_encoding: str | None = None) -> tuple[str, str, bool]:
    """Decode raw source bytes using PEP 263 declaration or fallback UTF-8.

    Returns:
        (decoded_text, encoding_used, has_bom)
    """
    try:
        encoding, has_bom = detect_encoding_and_bom(raw_bytes)
    except (TargetValidationError, UnicodeDecodeError, LookupError, ValueError):
        encoding, has_bom = "utf-8", False
    enc = declared_encoding or encoding
    try:
        if has_bom and enc.lower() == "utf-8":
            text = raw_bytes.decode("utf-8-sig")
        else:
            text = raw_bytes.decode(enc)
        return text, enc, has_bom
    except (UnicodeDecodeError, LookupError):
        # Fallback to UTF-8 with replacement
        return raw_bytes.decode("utf-8", errors="replace"), "utf-8", has_bom


def compile_ast_only(
    source: str | bytes,
    filename: str = "<string>",
) -> tuple[ast.AST | None, list[DiagnosticRecord], SyntaxErrorDetail | None]:
    """Validate Python source syntax using compile(..., flags=ast.PyCF_ONLY_AST).

    Ensures sys.dont_write_bytecode is set to True to prevent any bytecode emission.
    Never imports or executes the target source code.

    Returns:
        (ast_tree_or_none, diagnostics_list, detailed_error_or_none)
    """
    sys.dont_write_bytecode = True

    try:
        tree = compile(source, filename, mode="exec", flags=ast.PyCF_ONLY_AST)
        return tree, [], None
    except (SyntaxError, IndentationError) as err:
        diag, detail = parse_syntax_error(err, filename=filename)
        return None, [diag], detail
    except ValueError as err:
        diag = DiagnosticRecord(
            source="python_syntax",
            code="ValueError",
            message=str(err),
            severity=SeverityEnum.ERROR,
            start_line=1,
            start_col=1,
            end_line=1,
            end_col=1,
            fix_available=False,
        )
        detail = SyntaxErrorDetail(
            error_type="ValueError",
            message=str(err),
            line=1,
            column=1,
            end_line=1,
            end_column=1,
            source_line="",
            filename=filename,
        )
        return None, [diag], detail


def validate_python_syntax(
    target: TargetRecord,
    source_text: str | None = None,
) -> list[DiagnosticRecord]:
    """Validate target syntax without executing code or emitting bytecode.

    Used by PythonAdapter.check_syntax.
    """
    if source_text is not None:
        _, diags, _ = compile_ast_only(source_text, filename=target.path)
        return diags

    target_path = Path(target.absolute_path)
    if not target_path.is_file():
        return []

    try:
        raw_bytes = target_path.read_bytes()
    except OSError as exc:
        diag = DiagnosticRecord(
            source="python_syntax",
            code="IOError",
            message=f"Failed to read target file: {exc}",
            severity=SeverityEnum.ERROR,
            start_line=1,
            start_col=1,
            end_line=1,
            end_col=1,
            fix_available=False,
        )
        return [diag]

    # When passing bytes directly to compile with PyCF_ONLY_AST, Python
    # automatically parses PEP 263 headers.
    _, diags, _ = compile_ast_only(raw_bytes, filename=target.path)
    return diags
