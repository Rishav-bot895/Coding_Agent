"""Reporting, output rendering, and sanitization for localdev.

Provides exit code definitions, ANSI/VT sanitized terminal rendering,
and versioned RFC 8259 JSON envelopes preserving raw underlying data.
"""

from __future__ import annotations

from localdev.reporting.exit_codes import (
    ExitCode,
    get_exit_code_description,
    get_exit_code_name,
    is_success,
)
from localdev.reporting.json_reporter import (
    create_json_envelope,
    render_json_envelope,
    write_json_envelope,
)
from localdev.reporting.sanitizer import (
    safe_terminal_encode,
    sanitize_terminal_text,
)
from localdev.reporting.terminal import TerminalReporter

__all__ = [
    "ExitCode",
    "TerminalReporter",
    "create_json_envelope",
    "get_exit_code_description",
    "get_exit_code_name",
    "is_success",
    "render_json_envelope",
    "safe_terminal_encode",
    "sanitize_terminal_text",
    "write_json_envelope",
]
