"""Unit tests for P1-T1 contract, documentation, constants, and CLI help resolution.

Verifies:
1. Personal-project scope, trusted-code warning, single-target definition, and non-sandbox
   disclaimer are present in README.md and docs/security.md.
2. Constants integrity: token budget partition, exit codes, environment allowlist, and complexity vocabulary.
3. Import smoke tests for all top-level modules.
4. CLI parser and entry point execution for localdev --help.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import localdev
import localdev.cli
import localdev.constants as const


def test_import_smoke() -> None:
    """Assert top-level package and modules import cleanly without side-effects."""
    assert hasattr(localdev, "__version__")
    assert localdev.__version__ == "0.1.0"
    assert localdev.constants.APP_NAME == "localdev"
    assert hasattr(localdev.cli, "main")


def test_constants_token_budget_partition() -> None:
    """Verify that the token budget partition strictly matches the context window."""
    total = (
        const.PROMPT_BUDGET_TOKENS
        + const.OUTPUT_BUDGET_TOKENS
        + const.APPLICATION_SAFETY_MARGIN_TOKENS
    )
    assert total == const.CONTEXT_WINDOW_TOKENS, (
        f"Token partition mismatch: {const.PROMPT_BUDGET_TOKENS} + "
        f"{const.OUTPUT_BUDGET_TOKENS} + {const.APPLICATION_SAFETY_MARGIN_TOKENS} != "
        f"{const.CONTEXT_WINDOW_TOKENS}"
    )
    assert const.CONTEXT_WINDOW_TOKENS == 2048
    assert const.PROMPT_BUDGET_TOKENS == 1200
    assert const.OUTPUT_BUDGET_TOKENS == 600
    assert const.APPLICATION_SAFETY_MARGIN_TOKENS == 248


def test_constants_exit_codes() -> None:
    """Verify all 7 defined exit codes (0 through 6) are unique and correct."""
    expected = {
        const.EXIT_SUCCESS: 0,
        const.EXIT_TARGET_FAILURE: 1,
        const.EXIT_CLI_USAGE_ERROR: 2,
        const.EXIT_TARGET_IO_ERROR: 3,
        const.EXIT_TIMEOUT_RESOURCE_BREACH: 4,
        const.EXIT_INFERENCE_ERROR: 5,
        const.EXIT_ABSTENTION: 6,
    }
    for code, value in expected.items():
        assert code == value
    assert len(set(expected.values())) == 7


def test_constants_environment_allowlist() -> None:
    """Verify execution environment allowlist contains only required OS variables."""
    required = {
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "PATH",
        "PATHEXT",
        "TEMP",
        "TMP",
        "COMSPEC",
        "USERNAME",
    }
    assert set(const.ENV_ALLOWLIST) == required


def test_constants_complexity_vocabulary() -> None:
    """Verify closed complexity vocabulary matches the formal CPython contract."""
    expected_classes = (
        "O(1)",
        "O(log n)",
        "O(n)",
        "O(n log n)",
        "O(n²)",
        "O(n³)",
        "O(nm)",
        "O(2^n)",
        "UNKNOWN",
    )
    assert const.SUPPORTED_COMPLEXITY_CLASSES == expected_classes


def test_documentation_contracts_readme() -> None:
    """Verify README.md contains mandatory scope, trust, and non-sandbox statements."""
    readme_path = Path(__file__).parent.parent.parent / "README.md"
    assert readme_path.exists(), "README.md is missing"
    content = readme_path.read_text(encoding="utf-8").lower()

    # Scope statement
    assert "portfolio project" in content, "README.md missing personal portfolio project scope statement"
    assert "windows 11 x64 only" in content, "README.md missing Windows 11 x64 platform boundary"

    # Trust model
    assert "trusted" in content, "README.md missing trusted code warning"
    assert "user-owned" in content, "README.md missing user-owned code warning"

    # Non-sandbox disclaimer
    assert "not a security sandbox" in content or "not a sandbox" in content, (
        "README.md missing explicit non-sandbox disclaimer"
    )

    # Single-target definition
    assert "single-target" in content, "README.md missing single-target definition"


def test_documentation_contracts_security() -> None:
    """Verify docs/security.md contains mandatory scope, trust, and non-sandbox statements."""
    sec_path = Path(__file__).parent.parent.parent / "docs" / "security.md"
    assert sec_path.exists(), "docs/security.md is missing"
    content = sec_path.read_text(encoding="utf-8").lower()

    # Scope statement
    assert "portfolio project" in content, "docs/security.md missing portfolio project statement"
    assert "windows 11 x64 only" in content, "docs/security.md missing Windows 11 x64 platform statement"

    # Trust model
    assert "trusted" in content, "docs/security.md missing trusted code statement"
    assert "user-owned" in content, "docs/security.md missing user-owned code statement"

    # Non-sandbox disclaimer
    assert "not a security sandbox" in content, (
        "docs/security.md missing explicit non-sandbox disclaimer"
    )

    # Single-target definition
    assert "single-target" in content, "docs/security.md missing single-target definition"

    # Execution flags
    assert "-e -b -p" in content, "docs/security.md missing -E -B -P execution flags definition"


def test_cli_help_parser() -> None:
    """Verify CLI parser produces valid help text with all required commands."""
    parser = localdev.cli.create_parser()
    help_text = parser.format_help()

    commands = ["info", "detect", "analyse", "debug", "fix", "complexity", "profile"]
    for cmd in commands:
        assert cmd in help_text, f"Command '{cmd}' not found in CLI help output"


def test_cli_help_exit_code() -> None:
    """Verify invoking localdev.cli.main with --help raises SystemExit(0)."""
    with pytest.raises(SystemExit) as exc_info:
        localdev.cli.main(["--help"])
    assert exc_info.value.code == 0
def test_cli_subprocess_help() -> None:
    """Verify executing python -m localdev.cli --help produces zero exit code and expected usage."""
    result = subprocess.run(
        [sys.executable, "-m", "localdev.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "usage: localdev" in result.stdout
    assert "commands" in result.stdout


@pytest.mark.windows
def test_windows_platform_boundary() -> None:
    """Verify execution environment adheres to Windows 11 x64 platform boundary."""
    import platform

    assert sys.platform == "win32", "Test runner must be running on win32 platform"
    assert platform.machine().lower() in ("amd64", "x86_64"), "Target architecture must be x64"
