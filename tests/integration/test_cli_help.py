"""Integration tests for localdev CLI help text, version output, and entry points (P2-T1).

Verifies snapshot text assertions for top-level help, all seven subcommands,
version string, platform and safety disclaimers, and subprocess invocation.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from localdev.cli import ALL_COMMANDS, create_parser, main
from localdev.constants import APP_NAME, APP_VERSION, EXIT_CLI_USAGE_ERROR, EXIT_SUCCESS


# =============================================================================
# In-Process Help Text & Snapshot Assertions
# =============================================================================


def test_top_level_help_content(capsys: pytest.CaptureFixture[str]) -> None:
    """Top-level --help must render all subcommands and critical platform disclaimers."""
    parser = create_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--help"])
    assert exc_info.value.code == 0

    captured = capsys.readouterr()
    help_text = captured.out

    # Core identification & disclaimers
    assert APP_NAME in help_text
    assert "Windows 11 x64 only" in help_text
    assert "Operational limits terminate" in help_text
    assert "do NOT constitute a security sandbox" in help_text
    assert "user-owned or trusted code only" in help_text

    # Global options
    assert "--version" in help_text
    assert "--json" in help_text

    # All subcommands must appear
    for cmd in ALL_COMMANDS:
        assert cmd in help_text


@pytest.mark.parametrize("command", ALL_COMMANDS)
def test_subcommand_help_content(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    """Each subcommand --help must exit 0 and describe its parameters."""
    parser = create_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args([command, "--help"])
    assert exc_info.value.code == 0

    captured = capsys.readouterr()
    help_text = captured.out

    assert f"usage: {APP_NAME} {command}" in help_text
    assert "target" in help_text
    assert "--json" in help_text

    if command == "fix":
        assert "--apply" in help_text
        assert "--expected-stdout" in help_text
        assert "--expected-exit" in help_text
    elif command == "debug":
        assert "--stdin-file" in help_text
        assert "--" in help_text
    elif command == "profile":
        assert "--input" in help_text
    elif command == "complexity":
        assert "selector" in help_text.lower() or "func" in help_text


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """The --version flag outputs the exact application name and version."""
    parser = create_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--version"])
    assert exc_info.value.code == 0

    captured = capsys.readouterr()
    assert f"{APP_NAME} {APP_VERSION}" in captured.out


def test_main_help_dispatch(capsys: pytest.CaptureFixture[str]) -> None:
    """main(['--help']) raises SystemExit(0) and writes help to stdout."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert f"usage: {APP_NAME}" in captured.out


def test_main_no_args_dispatch(capsys: pytest.CaptureFixture[str]) -> None:
    """main([]) returns EXIT_CLI_USAGE_ERROR and writes help to stderr."""
    code = main([])
    assert code == EXIT_CLI_USAGE_ERROR
    captured = capsys.readouterr()
    assert f"usage: {APP_NAME}" in captured.err


# =============================================================================
# Subprocess Execution & CLI Entry Point
# =============================================================================


def test_subprocess_entrypoint_help() -> None:
    """Execute 'python -m localdev.cli --help' as real Windows subprocess."""
    proc = subprocess.run(
        [sys.executable, "-m", "localdev.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == EXIT_SUCCESS
    assert APP_NAME in proc.stdout
    assert "Windows 11 x64 only" in proc.stdout


def test_subprocess_entrypoint_version() -> None:
    """Execute 'python -m localdev.cli --version' as real Windows subprocess."""
    proc = subprocess.run(
        [sys.executable, "-m", "localdev.cli", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == EXIT_SUCCESS
    assert f"{APP_NAME} {APP_VERSION}" in proc.stdout


def test_subprocess_entrypoint_no_args() -> None:
    """Execute 'python -m localdev.cli' with no args; assert exit code 2."""
    proc = subprocess.run(
        [sys.executable, "-m", "localdev.cli"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == EXIT_CLI_USAGE_ERROR
    assert f"usage: {APP_NAME}" in proc.stderr


def test_subprocess_missing_target_error() -> None:
    """Execute 'python -m localdev.cli info' with missing target; assert clean error and code 2."""
    proc = subprocess.run(
        [sys.executable, "-m", "localdev.cli", "info"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == EXIT_CLI_USAGE_ERROR
    assert "Error: Command 'info' requires exactly one target file." in proc.stderr
    assert "Traceback" not in proc.stderr
