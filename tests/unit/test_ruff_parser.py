"""Unit tests for Ruff JSON parsing and diagnostics normalization.

Verifies:
1. Parsing of clean output ([]).
2. Normalization of single and multiple lint violations into DiagnosticRecord schemas.
3. Mapping of severities, rule codes, messages, and coordinates.
4. Safe handling of malformed or non-list JSON payloads.
5. Coordinate clamping invariants (1-based, inclusive, non-inverted).
6. Zero cache auditing helper.
"""

from __future__ import annotations

import json
from pathlib import Path

from localdev.languages.python.diagnostics import audit_zero_ruff_cache, parse_ruff_json
from localdev.schemas import DiagnosticRecord, SeverityEnum


def test_parse_clean_output() -> None:
    assert parse_ruff_json("[]") == []
    assert parse_ruff_json("") == []
    assert parse_ruff_json("   \n") == []


def test_parse_single_finding() -> None:
    sample = [
        {
            "code": "F401",
            "message": "`os` imported but unused",
            "name": "unused-import",
            "severity": "error",
            "location": {"row": 2, "column": 8},
            "end_location": {"row": 2, "column": 10},
            "fix": {
                "applicability": "safe",
                "message": "Remove unused import",
                "edits": [],
            },
        }
    ]
    diags = parse_ruff_json(json.dumps(sample))
    assert len(diags) == 1

    diag = diags[0]
    assert isinstance(diag, DiagnosticRecord)
    assert diag.source == "ruff"
    assert diag.code == "F401"
    assert diag.message == "`os` imported but unused"
    assert diag.severity == SeverityEnum.ERROR
    assert diag.start_line == 2
    assert diag.start_col == 8
    assert diag.end_line == 2
    assert diag.end_col == 10
    assert diag.fix_available is True


def test_parse_multiple_findings_with_different_severities() -> None:
    sample = [
        {
            "code": "I001",
            "message": "Import block is un-sorted or un-formatted",
            "severity": "warning",
            "location": {"row": 1, "column": 1},
            "end_location": {"row": 3, "column": 1},
            "fix": None,
        },
        {
            "code": "PLC0415",
            "message": "import should be at top-level",
            "severity": "info",
            "location": {"row": 10, "column": 5},
            "end_location": {"row": 10, "column": 15},
            "fix": None,
        },
    ]
    diags = parse_ruff_json(json.dumps(sample))
    assert len(diags) == 2

    assert diags[0].code == "I001"
    assert diags[0].severity == SeverityEnum.WARNING
    assert diags[0].fix_available is False
    assert diags[0].start_line == 1
    assert diags[0].end_line == 3

    assert diags[1].code == "PLC0415"
    assert diags[1].severity == SeverityEnum.INFO
    assert diags[1].start_line == 10
    assert diags[1].end_line == 10


def test_parse_malformed_json() -> None:
    diags = parse_ruff_json("{not valid json")
    assert len(diags) == 1
    assert diags[0].code == "MalformedOutput"
    assert "Failed to parse Ruff JSON" in diags[0].message
    assert diags[0].severity == SeverityEnum.WARNING


def test_parse_non_list_json() -> None:
    diags = parse_ruff_json('{"key": "value"}')
    assert diags == []


def test_coordinate_clamping_invariants() -> None:
    sample = [
        {
            "code": "E999",
            "message": "Syntax error",
            "location": {"row": 0, "column": 0},
            "end_location": {"row": -1, "column": -1},
        }
    ]
    diags = parse_ruff_json(json.dumps(sample))
    assert len(diags) == 1
    diag = diags[0]
    assert diag.start_line == 1
    assert diag.start_col == 1
    assert diag.end_line == 1
    assert diag.end_col >= diag.start_col


def test_audit_zero_ruff_cache(tmp_path: Path) -> None:
    # Initially clean
    assert audit_zero_ruff_cache(tmp_path) == []

    # Create dummy .ruff_cache
    cache_dir = tmp_path / "subdir" / ".ruff_cache"
    cache_dir.mkdir(parents=True)
    assert len(audit_zero_ruff_cache(tmp_path)) == 1

