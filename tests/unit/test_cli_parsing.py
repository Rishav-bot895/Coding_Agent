"""Unit tests for localdev CLI command parsing and exit semantics (P2-T1).

Verifies strict single-target enforcement, argument separator '--', function
selector syntax validation, secondary argument flags, stable exit codes,
and error reporting without Python tracebacks.
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout

import pytest

from localdev.cli import (
    ALL_COMMANDS,
    main,
    parse_cli_args,
    parse_selector,
)
from localdev.constants import (
    EXIT_ABSTENTION,
    EXIT_CLI_USAGE_ERROR,
    EXIT_INFERENCE_ERROR,
    EXIT_SUCCESS,
    EXIT_TARGET_FAILURE,
    EXIT_TARGET_IO_ERROR,
    EXIT_TIMEOUT_RESOURCE_BREACH,
)
from localdev.errors import (
    CliUsageError,
    MalformedSelectorError,
    MultipleTargetsError,
)
from localdev.reporting.exit_codes import (
    ExitCode,
    get_exit_code_description,
    get_exit_code_name,
    is_success,
)

# =============================================================================
# Exit Codes & Helpers
# =============================================================================


def test_exit_codes_mapping() -> None:
    """Assert all exit codes map to their frozen integer values."""
    assert ExitCode.SUCCESS == EXIT_SUCCESS == 0
    assert ExitCode.TARGET_FAILURE == EXIT_TARGET_FAILURE == 1
    assert ExitCode.CLI_USAGE_ERROR == EXIT_CLI_USAGE_ERROR == 2
    assert ExitCode.TARGET_IO_ERROR == EXIT_TARGET_IO_ERROR == 3
    assert ExitCode.TIMEOUT_RESOURCE_BREACH == EXIT_TIMEOUT_RESOURCE_BREACH == 4
    assert ExitCode.INFERENCE_ERROR == EXIT_INFERENCE_ERROR == 5
    assert ExitCode.ABSTENTION == EXIT_ABSTENTION == 6


def test_exit_code_helpers() -> None:
    """Verify name and description helpers for exit codes."""
    assert get_exit_code_name(0) == "SUCCESS"
    assert get_exit_code_name(2) == "CLI_USAGE_ERROR"
    assert get_exit_code_name(999) == "UNKNOWN"

    assert is_success(0) is True
    assert is_success(1) is False
    assert is_success(2) is False

    assert "completed successfully" in get_exit_code_description(0).lower()
    assert "CLI usage" in get_exit_code_description(2)
    assert "Unrecognized" in get_exit_code_description(999)


# =============================================================================
# Target Cardinality Enforcement
# =============================================================================


@pytest.mark.parametrize("command", ALL_COMMANDS)
def test_missing_target_rejected(command: str) -> None:
    """Every command must reject execution when no target file is supplied."""
    with pytest.raises(CliUsageError) as exc_info:
        parse_cli_args([command])
    assert f"Command '{command}' requires exactly one target file." in str(exc_info.value)

    # Verify main() returns exit code 2 and outputs clean error
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main([command])
    assert code == EXIT_CLI_USAGE_ERROR
    assert f"Error: Command '{command}' requires exactly one target file." in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()


@pytest.mark.parametrize("command", [c for c in ALL_COMMANDS if c != "debug"])
def test_multiple_targets_rejected_non_debug(command: str) -> None:
    """Non-debug commands must strictly reject multiple positional targets."""
    with pytest.raises(MultipleTargetsError) as exc_info:
        parse_cli_args([command, "target1.py", "target2.py"])
    assert "Multiple targets or unrecognized positional arguments" in str(exc_info.value)
    assert "target2.py" in str(exc_info.value)

    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main([command, "target1.py", "target2.py"])
    assert code == EXIT_CLI_USAGE_ERROR
    assert "Error: Multiple targets or unrecognized positional arguments" in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()


def test_debug_multiple_targets_without_separator_rejected() -> None:
    """Debug command must reject multiple positional args if '--' separator is omitted."""
    with pytest.raises(MultipleTargetsError) as exc_info:
        parse_cli_args(["debug", "target.py", "extra.py"])
    assert "Use '--' to pass arguments to the target script" in str(exc_info.value)

    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main(["debug", "target.py", "extra.py"])
    assert code == EXIT_CLI_USAGE_ERROR
    assert "Use '--' to pass arguments to the target script" in stderr.getvalue()


def test_debug_with_separator_passes_target_args() -> None:
    """Debug command accepts script arguments following '--' separator."""
    parsed = parse_cli_args(["debug", "script.py", "--", "-v", "--flag", "val", "pos"])
    assert parsed.command == "debug"
    assert parsed.target == "script.py"
    assert parsed.target_file == "script.py"
    assert parsed.target_args == ["-v", "--flag", "val", "pos"]

    # Trailing separator with no args is valid
    parsed_empty = parse_cli_args(["debug", "script.py", "--"])
    assert parsed_empty.target_args == []


@pytest.mark.parametrize("command", [c for c in ALL_COMMANDS if c != "debug"])
def test_arguments_after_separator_rejected_for_non_debug(command: str) -> None:
    """Only 'debug' accepts '--' arguments; all other commands must reject them."""
    with pytest.raises(CliUsageError) as exc_info:
        parse_cli_args([command, "target.py", "--", "extra_arg"])
    assert f"Command '{command}' does not accept arguments after '--'." in str(exc_info.value)

    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main([command, "target.py", "--", "extra_arg"])
    assert code == EXIT_CLI_USAGE_ERROR
    assert f"Error: Command '{command}' does not accept arguments after '--'." in stderr.getvalue()


# =============================================================================
# Selector Syntax Parsing
# =============================================================================


@pytest.mark.parametrize("command", ["complexity", "profile"])
def test_valid_selectors_allowed(command: str) -> None:
    """Complexity and profile commands accept function and method selectors."""
    # Top-level function
    p1 = parse_cli_args([command, "module.py::compute"])
    assert p1.target == "module.py::compute"
    assert p1.target_file == "module.py"
    assert p1.selector == "compute"

    # Class method
    p2 = parse_cli_args([command, "module.py::Optimizer.step"])
    assert p2.target == "module.py::Optimizer.step"
    assert p2.target_file == "module.py"
    assert p2.selector == "Optimizer.step"

    # Whole file without selector
    p3 = parse_cli_args([command, "module.py"])
    assert p3.target == "module.py"
    assert p3.target_file == "module.py"
    assert p3.selector is None


@pytest.mark.parametrize("command", ["info", "detect", "analyse", "debug", "fix"])
def test_selectors_forbidden_on_whole_file_commands(command: str) -> None:
    """Commands that operate on whole files must reject function selectors."""
    with pytest.raises(CliUsageError) as exc_info:
        parse_cli_args([command, "module.py::compute"])
    assert "only supported for 'complexity' and 'profile'" in str(exc_info.value)

    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main([command, "module.py::compute"])
    assert code == EXIT_CLI_USAGE_ERROR
    assert "only supported for 'complexity' and 'profile'" in stderr.getvalue()


@pytest.mark.parametrize(
    ("raw_selector", "error_substring"),
    [
        ("file.py::", "missing function name after '::'"),
        ("::compute", "missing file path before '::'"),
        ("file.py::::compute", "multiple '::' separators are not permitted"),
        ("file.py::func1::func2", "multiple '::' separators are not permitted"),
        ("file.py::123invalid", "'123invalid' is not a valid Python identifier"),
        ("file.py::bad-identifier", "'bad-identifier' is not a valid Python identifier"),
        ("file.py::func()", "'func()' is not a valid Python identifier"),
        ("file.py::Class..method", "empty identifier segment"),
        ("file.py::Class.SubClass.method", "nested selectors beyond 'ClassName.method_name' are not supported"),
    ],
)
def test_malformed_selectors_rejected(raw_selector: str, error_substring: str) -> None:
    """Malformed selectors must fail with specific, actionable errors."""
    with pytest.raises(MalformedSelectorError) as exc_info:
        parse_selector(raw_selector, allow_selector=True)
    assert error_substring in str(exc_info.value)

    # Test via parse_cli_args
    with pytest.raises(MalformedSelectorError) as exc_info_cli:
        parse_cli_args(["complexity", raw_selector])
    assert error_substring in str(exc_info_cli.value)


# =============================================================================
# Command-Specific Flags & Prohibitions
# =============================================================================


def test_fix_apply_flag() -> None:
    """The --apply flag is accepted on fix and provides noninteractive write authority."""
    parsed_default = parse_cli_args(["fix", "target.py"])
    assert parsed_default.apply is False

    parsed_applied = parse_cli_args(["fix", "target.py", "--apply"])
    assert parsed_applied.apply is True


@pytest.mark.parametrize("command", [c for c in ALL_COMMANDS if c != "fix"])
def test_apply_flag_rejected_on_non_fix_commands(command: str) -> None:
    """The --apply flag must be rejected with clean usage error if used without fix."""
    with pytest.raises(CliUsageError) as exc_info:
        parse_cli_args([command, "target.py", "--apply"])
    assert "The '--apply' flag is only valid for the 'fix' command." in str(exc_info.value)

    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main([command, "target.py", "--apply"])
    assert code == EXIT_CLI_USAGE_ERROR
    assert "The '--apply' flag is only valid for the 'fix' command." in stderr.getvalue()


def test_apply_at_top_level_without_fix_rejected() -> None:
    """Using --apply without the fix command produces clean usage error."""
    with pytest.raises(CliUsageError) as exc_info:
        parse_cli_args(["--apply", "info", "target.py"])
    assert "The '--apply' flag is only valid for the 'fix' command." in str(exc_info.value)

    with pytest.raises(CliUsageError) as exc_info_bare:
        parse_cli_args(["--apply"])
    assert "The '--apply' flag is only valid for the 'fix' command." in str(exc_info_bare.value)


@pytest.mark.parametrize("command", ALL_COMMANDS)
def test_force_flag_prohibited(command: str) -> None:
    """No --force bypass flag exists anywhere; must be rejected cleanly."""
    with pytest.raises(CliUsageError) as exc_info:
        parse_cli_args([command, "target.py", "--force"])
    assert "--force" in str(exc_info.value)
    assert "has no --force bypass flag" in str(exc_info.value)

    # Also top-level
    with pytest.raises(CliUsageError):
        parse_cli_args(["--force", command, "target.py"])


# =============================================================================
# Secondary Argument Flags
# =============================================================================


def test_secondary_flags_parsing() -> None:
    """Secondary data flags are parsed correctly on their designated commands."""
    # profile --input
    p_prof = parse_cli_args(["profile", "target.py::fn", "--input", "inputs.json"])
    assert p_prof.input_file == "inputs.json"

    # fix --expected-stdout and --expected-exit
    p_fix = parse_cli_args(
        ["fix", "target.py", "--expected-stdout", "result: 42\n", "--expected-exit", "0"]
    )
    assert p_fix.expected_stdout == "result: 42\n"
    assert p_fix.expected_exit == 0

    # debug --stdin-file
    p_debug = parse_cli_args(["debug", "target.py", "--stdin-file", "input.txt"])
    assert p_debug.stdin_file == "input.txt"


def test_secondary_flags_rejected_on_wrong_commands() -> None:
    """Secondary flags passed to non-owning commands are rejected."""
    with pytest.raises(CliUsageError):
        parse_cli_args(["info", "target.py", "--input", "inputs.json"])

    with pytest.raises(CliUsageError):
        parse_cli_args(["analyse", "target.py", "--stdin-file", "in.txt"])

    with pytest.raises(CliUsageError):
        parse_cli_args(["debug", "target.py", "--expected-stdout", "ok"])


def test_invalid_int_for_expected_exit() -> None:
    """Passing non-integer string to --expected-exit raises clean usage error."""
    with pytest.raises(CliUsageError) as exc_info:
        parse_cli_args(["fix", "target.py", "--expected-exit", "invalid_num"])
    assert "invalid int value" in str(exc_info.value)


# =============================================================================
# Global Flags & Invocation Dispatch
# =============================================================================


def test_json_flag_placement() -> None:
    """The --json flag is accepted both before and after the subcommand."""
    p1 = parse_cli_args(["--json", "info", "target.py"])
    assert p1.json_output is True

    p2 = parse_cli_args(["info", "target.py", "--json"])
    assert p2.json_output is True

    p3 = parse_cli_args(["info", "target.py"])
    assert p3.json_output is False


def test_unknown_command() -> None:
    """Unknown commands produce actionable error listing valid commands."""
    with pytest.raises(CliUsageError) as exc_info:
        parse_cli_args(["execute", "target.py"])
    assert "Unknown command 'execute'" in str(exc_info.value)
    assert "Available commands: info, detect, analyse, debug, fix, complexity, profile" in str(
        exc_info.value
    )


def test_no_arguments_prints_help() -> None:
    """Invoking localdev with no arguments prints help to stderr and returns code 2."""
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = main([])
    assert code == EXIT_CLI_USAGE_ERROR
    assert "usage: localdev" in stderr.getvalue()


def test_successful_main_dispatch() -> None:
    """Valid invocation returns EXIT_SUCCESS and writes initialization line."""
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        code = main(["debug", "script.py"])
    assert code == EXIT_SUCCESS
    assert "localdev debug: initialized for target 'script.py'." in stdout.getvalue()

