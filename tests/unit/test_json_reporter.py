"""Unit tests for JSON envelope serialization and raw data preservation (P2-T4).

Verifies strict RFC 8259 compliance, fixed schema_version: "1.0", and verbatim
preservation of raw underlying strings (ANSI sequences, null bytes, control characters)
without lossy terminal display sanitization.
"""

from __future__ import annotations

import io
import json

from localdev.reporting.json_reporter import (
    create_json_envelope,
    render_json_envelope,
    write_json_envelope,
)
from localdev.schemas import DiagnosticRecord, SeverityEnum


def test_create_json_envelope_schema_version() -> None:
    """Every generated JSON envelope must specify fixed schema_version='1.0'."""
    envelope = create_json_envelope(
        command="analyse",
        success=True,
        target_path="script.py",
        data={"items": [1, 2, 3]},
    )
    assert envelope.schema_version == "1.0"
    assert envelope.command == "analyse"
    assert envelope.success is True
    assert envelope.target_path == "script.py"
    assert envelope.data == {"items": [1, 2, 3]}


def test_raw_strings_verbatim_preservation() -> None:
    """JSON serialization must preserve exact raw bytes/control sequences verbatim."""
    raw_hostile_stdout = (
        "Process output:\x00 null byte, \x1b[31;1mRed text\x1b[0m, \x1b[2JClear\n"
        "\x1b]0;Title\x07New Title\tTabbed\r\n"
    )

    envelope = create_json_envelope(
        command="debug",
        success=False,
        target_path="faulty.py",
        data={"stdout": raw_hostile_stdout},
        errors=["Failed with exit code 1"],
    )

    rendered_json = render_json_envelope(envelope)

    # Parse back with standard json library
    parsed = json.loads(rendered_json)
    assert parsed["schema_version"] == "1.0"
    assert parsed["command"] == "debug"
    assert parsed["success"] is False

    # Assert exact verbatim preservation of hostile/control characters in data payload
    assert parsed["data"]["stdout"] == raw_hostile_stdout


def test_write_json_envelope_to_stream() -> None:
    """write_json_envelope writes valid JSON followed by newline to target stream."""
    stream = io.StringIO()
    envelope = create_json_envelope(
        command="info",
        success=True,
        target_path="app.py",
        limitations=["Only trusted code is supported"],
    )

    write_json_envelope(envelope, stream)
    output = stream.getvalue()

    assert output.endswith("\n")
    parsed = json.loads(output)
    assert parsed["command"] == "info"
    assert parsed["limitations"] == ["Only trusted code is supported"]


def test_envelope_with_pydantic_submodels() -> None:
    """JSON envelope seamlessly serializes nested Pydantic models."""
    diagnostic = DiagnosticRecord(
        source="ruff",
        code="E501",
        message="Line too long (95 > 88)",
        severity=SeverityEnum.WARNING,
        start_line=10,
        start_col=1,
        end_line=10,
        end_col=95,
        fix_available=False,
    )

    envelope = create_json_envelope(
        command="analyse",
        success=True,
        target_path="long_lines.py",
        data=[diagnostic],
    )

    rendered = render_json_envelope(envelope)
    parsed = json.loads(rendered)

    assert len(parsed["data"]) == 1
    assert parsed["data"][0]["code"] == "E501"
    assert parsed["data"][0]["severity"] == "warning"

