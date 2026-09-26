"""Command-line interface entry point for localdev.

Provides command parsing, help text, and dispatch for native Windows single-file
offline Python analysis, debugging, fixing, complexity estimation, and profiling.
Enforces single-target constraints, validates secondary data flags, parses function
selectors, and defines stable exit semantics without tracebacks on user errors.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final, NoReturn

from localdev.agent.orchestrator import Orchestrator
from localdev.agent.permissions import validate_target
from localdev.agent.session import Session
from localdev.constants import (
    APP_NAME,
    APP_VERSION,
    EXIT_ABSTENTION,
    EXIT_CLI_USAGE_ERROR,
    EXIT_SUCCESS,
    EXIT_TARGET_FAILURE,
)
from localdev.errors import (
    CliUsageError,
    LocaldevError,
    MalformedSelectorError,
    MultipleTargetsError,
)
from localdev.reporting.json_reporter import write_json_envelope
from localdev.reporting.terminal import TerminalReporter
from localdev.schemas import (
    AnalysisReport,
    ComplexityAbstentionReason,
    ComplexityReport,
    DetectionResult,
    DiagnosisAbstention,
    ExecutionResult,
    FixReport,
    JsonEnvelope,
    ProfileReport,
    TargetInfoRecord,
)

COMMANDS_ALLOWING_SELECTORS: Final[frozenset[str]] = frozenset({"complexity", "profile"})
ALL_COMMANDS: Final[tuple[str, ...]] = (
    "info",
    "detect",
    "analyse",
    "debug",
    "fix",
    "complexity",
    "profile",
)


@dataclass(frozen=True)
class ParsedCliCommand:
    """Strongly typed, validated representation of a localdev CLI invocation."""

    command: str
    target: str
    target_file: str
    selector: str | None = None
    json_output: bool = False
    apply: bool = False
    expected_stdout: str | None = None
    expected_stdout_contains: str | None = None
    expected_exit: int | None = None
    stdin_file: str | None = None
    target_args: list[str] = field(default_factory=list)
    input_file: str | None = None
    keep_session: bool = False
    timeout: float | None = None
    fail_on_job_failure: bool = False
    diagnose: bool = False
    propose_only: bool = False
    warmup: int | None = None
    measured: int | None = None



def parse_selector(target: str, allow_selector: bool = True) -> tuple[str, str | None]:
    """Parse a target string into (target_file_path, function_selector_or_none).

    Syntax:
        - Whole file: ``path/to/script.py`` -> (``path/to/script.py``, None)
        - Function selector: ``script.py::func_name`` -> (``script.py``, ``func_name``)
        - Method selector: ``script.py::Class.method`` -> (``script.py``, ``Class.method``)

    Args:
        target: Raw target string as passed on CLI.
        allow_selector: Whether function selectors are permitted for this command.

    Returns:
        Tuple of (target_file_path, function_selector_or_none).

    Raises:
        CliUsageError: If selector is provided on a command that forbids it.
        MalformedSelectorError: If syntax is invalid, identifier is invalid, or
            empty components exist.
    """
    if "::" not in target:
        cleaned = target.strip()
        if not cleaned:
            raise CliUsageError("Target file path cannot be empty.")
        return cleaned, None

    if not allow_selector:
        raise CliUsageError(
            f"Target '{target}' contains a function selector ('::'), but selectors are "
            "only supported for 'complexity' and 'profile' commands. "
            "Only whole-file targets are supported for this command."
        )

    if target.count("::") != 1:
        raise MalformedSelectorError(
            f"Malformed selector '{target}': multiple '::' separators are not permitted."
        )

    file_part, func_part = target.split("::")
    file_part = file_part.strip()
    func_part = func_part.strip()

    if not file_part:
        raise MalformedSelectorError(
            f"Malformed selector '{target}': missing file path before '::'."
        )

    if not func_part:
        raise MalformedSelectorError(
            f"Malformed selector '{target}': missing function name after '::'."
        )

    segments = func_part.split(".")
    for segment in segments:
        if not segment:
            raise MalformedSelectorError(
                f"Malformed selector '{target}': empty identifier segment in '{func_part}'."
            )
        if not segment.isidentifier():
            raise MalformedSelectorError(
                f"Malformed selector '{target}': '{segment}' is not a valid Python identifier."
            )

    if len(segments) > 2:
        raise MalformedSelectorError(
            f"Malformed selector '{target}': nested selectors beyond 'ClassName.method_name' "
            "are not supported in the MVP."
        )

    return file_part, func_part


class LocaldevArgumentParser(argparse.ArgumentParser):
    """Custom ArgumentParser that raises CliUsageError instead of calling sys.exit.

    Ensures that unexpected arguments, missing options, and syntax misuse produce
    clean, actionable errors without printing uncaught Python tracebacks.
    """

    active_command: str | None = None

    def error(self, message: str) -> NoReturn:
        cmd_name = self.active_command or (self.prog.split()[-1] if self.prog else APP_NAME)

        if "invalid choice:" in message and "<command>" in message:
            # Extract choice from standard argparse message
            raw_choice = message.split("invalid choice:", 1)[1].split("(", 1)[0].strip()
            raise CliUsageError(
                f"Unknown command {raw_choice}. Available commands: {', '.join(ALL_COMMANDS)}."
            )

        if "unrecognized arguments:" in message:
            unrecognized = message.split("unrecognized arguments:", 1)[1].strip()

            if "--force" in unrecognized:
                raise CliUsageError(
                    "Unrecognized argument: --force (localdev has no --force bypass flag)."
                )

            if "--apply" in unrecognized:
                raise CliUsageError("The '--apply' flag is only valid for the 'fix' command.")

            # If positional arguments (not starting with '-') are present
            tokens = unrecognized.split()
            if any(not tok.startswith("-") for tok in tokens):
                if cmd_name == "debug":
                    raise MultipleTargetsError(
                        f"Multiple targets or unseparated arguments provided: {unrecognized}. "
                        "Use '--' to pass arguments to the target script (e.g. 'localdev debug script.py -- arg1')."
                    )
                raise MultipleTargetsError(
                    f"Multiple targets or unrecognized positional arguments provided: {unrecognized}. "
                    "Exactly one target file is permitted."
                )

            raise CliUsageError(f"Unrecognized arguments: {unrecognized}")

        if "the following arguments are required:" in message:
            req = message.split("the following arguments are required:", 1)[1].strip()
            if "target" in req:
                raise CliUsageError(f"Command '{cmd_name}' requires exactly one target file.")
            raise CliUsageError(f"Missing required argument: {req}")

        raise CliUsageError(message)


def create_parser() -> LocaldevArgumentParser:
    """Construct the primary top-level CLI argument parser."""
    shared_options = LocaldevArgumentParser(add_help=False)
    shared_options.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Output structured RFC 8259 JSON envelope instead of human-readable text.",
    )
    shared_options.add_argument(
        "--keep-session",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Retain temporary session directory after command completion for debugging.",
    )

    parser = LocaldevArgumentParser(
        prog=APP_NAME,
        description=(
            "localdev: Native Windows single-file offline Python coding agent.\n"
            "Personal portfolio project for user-owned or trusted code only.\n"
            "Supported platform: Windows 11 x64 only. Operational limits terminate\n"
            "runaways but do NOT constitute a security sandbox."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[shared_options],
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"{APP_NAME} {APP_VERSION}",
        help="Show application version and exit.",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        description="Available single-target inspection, analysis, and repair commands",
        metavar="<command>",
        parser_class=LocaldevArgumentParser,
    )

    # info
    p_info = subparsers.add_parser(
        "info",
        help="Inspect single target file metadata, encoding, BOM, and line endings.",
        description="Inspect single target file metadata, encoding, BOM, and line endings.",
        parents=[shared_options],
    )
    p_info.add_argument("target", help="Explicit target Python source file.")

    # detect
    p_detect = subparsers.add_parser(
        "detect",
        help="Perform non-executing language detection (extension, shebang, AST).",
        description="Perform non-executing language detection (extension, shebang, AST).",
        parents=[shared_options],
    )
    p_detect.add_argument("target", help="Explicit target file to classify.")

    # analyse
    p_analyse = subparsers.add_parser(
        "analyse",
        help="Run static syntax check, AST fact extraction, and isolated Ruff linting.",
        description="Run static syntax check, AST fact extraction, and isolated Ruff linting.",
        parents=[shared_options],
    )
    p_analyse.add_argument("target", help="Explicit target Python source file.")
    p_analyse.add_argument(
        "--diagnose",
        action="store_true",
        help="Request evidence-grounded bug diagnosis from local SLM alongside deterministic static analysis.",
    )

    # debug
    p_debug = subparsers.add_parser(
        "debug",
        help="Execute target in controlled runtime (-E -B -P) and parse tracebacks.",
        description=(
            "Execute target in controlled runtime (-E -B -P) and parse tracebacks.\n"
            "Pass arguments to target script after '--' (e.g. 'localdev debug script.py -- arg1')."
        ),
        parents=[shared_options],
    )
    p_debug.add_argument("target", help="Explicit target Python source file.")
    p_debug.add_argument(
        "--diagnose",
        action="store_true",
        help="Request evidence-grounded bug diagnosis from local SLM alongside deterministic runtime traceback evidence.",
    )
    p_debug.add_argument(
        "--stdin-file",
        help="Path to file supplying stdin for execution.",
    )
    p_debug.add_argument(
        "--timeout",
        type=float,
        help="Maximum execution duration in seconds before termination.",
    )
    p_debug.add_argument(
        "--fail-on-job-failure",
        action="store_true",
        help="Abort execution with non-zero exit code if Windows Job Object creation or assignment fails.",
    )

    # fix
    p_fix = subparsers.add_parser(
        "fix",
        help="Diagnose failure with local SLM and propose guarded structured patch.",
        description="Diagnose failure with local SLM and propose guarded structured patch.",
        parents=[shared_options],
    )
    p_fix.add_argument("target", help="Explicit target Python source file.")
    p_fix.add_argument(
        "--apply",
        action="store_true",
        help="Apply validated patch noninteractively without confirmation prompt.",
    )
    p_fix.add_argument(
        "--expected-stdout",
        help="Expected stdout string for Level D behavioral validation.",
    )
    p_fix.add_argument(
        "--expected-stdout-contains",
        help="Substring that must be present in stdout for Level D behavioral validation.",
    )
    p_fix.add_argument(
        "--expected-exit",
        type=int,
        help="Expected exit code for Level D behavioral validation.",
    )
    p_fix.add_argument(
        "--stdin-file",
        help="Path to file supplying stdin for execution.",
    )
    p_fix.add_argument(
        "--timeout",
        type=float,
        help="Maximum execution duration in seconds before termination.",
    )
    p_fix.add_argument(
        "--fail-on-job-failure",
        action="store_true",
        help="Abort execution with non-zero exit code if Windows Job Object creation or assignment fails.",
    )
    p_fix.add_argument(
        "--propose-only",
        "--propose-fix",
        dest="propose_only",
        action="store_true",
        help="Display proposed patch and validation results without prompting to apply.",
    )

    # complexity
    p_complexity = subparsers.add_parser(
        "complexity",
        help="Perform static time, auxiliary-space, and output-space analysis.",
        description="Perform static time, auxiliary-space, and output-space analysis.",
        parents=[shared_options],
    )
    p_complexity.add_argument(
        "target",
        help="Explicit target Python source file (or file.py::func selector).",
    )

    # profile
    p_profile = subparsers.add_parser(
        "profile",
        help="Profile import cost, invocation latency, tracemalloc allocations, and RSS.",
        description="Profile import cost, invocation latency, tracemalloc allocations, and RSS.",
        parents=[shared_options],
    )
    p_profile.add_argument(
        "target",
        help="Explicit target Python source file (or file.py::func selector).",
    )
    p_profile.add_argument(
        "--input",
        dest="input_file",
        help="Path to JSON file supplying positional/keyword arguments to function.",
    )
    p_profile.add_argument(
        "--warmup",
        type=int,
        help="Number of warm-up iterations (default: 2).",
    )
    p_profile.add_argument(
        "--measured",
        type=int,
        help="Number of measured iterations (default: 7).",
    )
    p_profile.add_argument(
        "--timeout",
        type=float,
        help="Maximum execution duration in seconds before termination.",
    )
    p_profile.add_argument(
        "--fail-on-job-failure",
        action="store_true",
        help="Abort execution with non-zero exit code if Windows Job Object creation or assignment fails.",
    )

    return parser


def parse_cli_args(argv: Sequence[str]) -> ParsedCliCommand:
    """Parse and validate command-line arguments according to the localdev contract.

    Args:
        argv: List of argument strings (excluding executable name).

    Returns:
        Validated ParsedCliCommand object.

    Raises:
        CliUsageError: On syntax errors, invalid flags, missing arguments, or
            contract violations.
    """
    if not argv:
        raise CliUsageError(f"No command specified. Available commands: {', '.join(ALL_COMMANDS)}.")

    # Prohibition of --force
    if "--force" in argv:
        raise CliUsageError("Unrecognized argument: --force (localdev has no --force bypass flag).")

    # Reject --apply if command is not 'fix'
    if "--apply" in argv:
        # Check if 'fix' is present before '--'
        tokens_before_sep = list(argv)
        if "--" in tokens_before_sep:
            tokens_before_sep = tokens_before_sep[: tokens_before_sep.index("--")]
        if "fix" not in tokens_before_sep:
            raise CliUsageError("The '--apply' flag is only valid for the 'fix' command.")

    # Separate arguments after '--'
    if "--" in argv:
        sep_idx = argv.index("--")
        before_sep = list(argv[:sep_idx])
        after_sep = list(argv[sep_idx + 1:])
    else:
        before_sep = list(argv)
        after_sep = []

    cmd = next((tok for tok in before_sep if tok in ALL_COMMANDS), None)
    parser = create_parser()
    parser.active_command = cmd
    parser.set_defaults(json=False)
    args = parser.parse_args(before_sep)

    if not args.command:
        raise CliUsageError(
            f"No command specified. Available commands: {', '.join(ALL_COMMANDS)}."
        )

    # Validate '--' separator usage: only 'debug' accepts target arguments after '--'
    if "--" in argv and args.command != "debug":
        raise CliUsageError(f"Command '{args.command}' does not accept arguments after '--'.")

    # Selector parsing: permitted only on 'complexity' and 'profile'
    allow_selector = args.command in COMMANDS_ALLOWING_SELECTORS
    target_str = getattr(args, "target", "")
    target_file, selector = parse_selector(target_str, allow_selector=allow_selector)

    # Fix-specific noninteractive write authority
    apply_flag = bool(getattr(args, "apply", False))
    if apply_flag and args.command != "fix":
        raise CliUsageError("The '--apply' flag is only valid for the 'fix' command.")

    json_flag = bool(getattr(args, "json", False)) or ("--json" in before_sep)
    keep_session_flag = bool(getattr(args, "keep_session", False)) or (
        "--keep-session" in before_sep
    )

    return ParsedCliCommand(
        command=args.command,
        target=target_str,
        target_file=target_file,
        selector=selector,
        json_output=json_flag,
        apply=apply_flag,
        expected_stdout=getattr(args, "expected_stdout", None),
        expected_stdout_contains=getattr(args, "expected_stdout_contains", None),
        expected_exit=getattr(args, "expected_exit", None),
        stdin_file=getattr(args, "stdin_file", None),
        target_args=after_sep,
        input_file=getattr(args, "input_file", None),
        keep_session=keep_session_flag,
        timeout=getattr(args, "timeout", None),
        fail_on_job_failure=bool(getattr(args, "fail_on_job_failure", False)),
        diagnose=bool(getattr(args, "diagnose", False)),
        propose_only=bool(getattr(args, "propose_only", False)),
        warmup=getattr(args, "warmup", None),
        measured=getattr(args, "measured", None),
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI console script entry point for localdev."""
    if argv is None:
        argv = sys.argv[1:]

    # When invoked with no arguments, print help to stderr and exit with code 2
    if not argv:
        parser = create_parser()
        parser.print_help(sys.stderr)
        return EXIT_CLI_USAGE_ERROR

    is_json = "--json" in argv
    parsed: ParsedCliCommand | None = None

    try:
        parsed = parse_cli_args(argv)
        is_json = parsed.json_output

        # For commands not yet wired, preserve the stub initialization line
        if parsed.command not in ("info", "detect", "analyse", "debug", "fix", "complexity", "profile"):
            sys.stdout.write(
                f"localdev {parsed.command}: initialized for target '{parsed.target_file}'.\n"
            )
            return EXIT_SUCCESS

        # Validate target file
        target_record = validate_target(parsed.target_file)

        # Isolated session management with strict cleanup
        with Session(target_record=target_record, keep_session=parsed.keep_session) as session:
            orchestrator = Orchestrator(session=session)

            if parsed.command == "info":
                info_record = orchestrator.get_info(target_record)
                if parsed.json_output:
                    envelope = JsonEnvelope[TargetInfoRecord](
                        command="info",
                        success=True,
                        target_path=target_record.path,
                        data=info_record,
                    )
                    write_json_envelope(envelope, sys.stdout)
                else:
                    reporter = TerminalReporter(sys.stdout)
                    reporter.write(reporter.render_info(info_record))
                return EXIT_SUCCESS

            if parsed.command == "detect":
                detection = orchestrator.detect(target_record)
                if parsed.json_output:
                    det_envelope = JsonEnvelope[DetectionResult](
                        command="detect",
                        success=True,
                        target_path=target_record.path,
                        data=detection,
                    )
                    write_json_envelope(det_envelope, sys.stdout)
                else:
                    reporter = TerminalReporter(sys.stdout)
                    reporter.write(reporter.render_detect(target_record.path, detection))
                return EXIT_SUCCESS

            if parsed.command == "analyse":
                analysis_report = orchestrator.analyse(
                    target_record,
                    diagnose=parsed.diagnose,
                )
                if parsed.json_output:
                    analysis_limitations: list[str] = []
                    if (
                        parsed.diagnose
                        and isinstance(analysis_report.diagnosis, DiagnosisAbstention)
                    ):
                        analysis_limitations.append(
                            f"Local SLM diagnosis abstained: {analysis_report.diagnosis.details}"
                        )
                    analysis_envelope = JsonEnvelope[AnalysisReport](
                        command="analyse",
                        success=analysis_report.syntax_valid,
                        target_path=target_record.path,
                        data=analysis_report,
                        errors=[d.message for d in analysis_report.syntax_diagnostics],
                        limitations=analysis_limitations,
                    )
                    write_json_envelope(analysis_envelope, sys.stdout)
                else:
                    reporter = TerminalReporter(sys.stdout)
                    reporter.write(reporter.render_analyse(analysis_report))
                return EXIT_SUCCESS if analysis_report.syntax_valid else EXIT_TARGET_FAILURE

            if parsed.command == "debug":
                exec_result = orchestrator.debug(
                    target=target_record,
                    target_args=parsed.target_args,
                    stdin_file=parsed.stdin_file,
                    timeout=parsed.timeout,
                    fail_on_job_failure=parsed.fail_on_job_failure,
                    diagnose=parsed.diagnose,
                )
                is_success = exec_result.exit_code == 0 and not exec_result.timed_out
                if parsed.json_output:
                    errors_list: list[str] = []
                    debug_limitations: list[str] = []
                    if exec_result.error_signature:
                        errors_list.append(
                            f"{exec_result.error_signature.exception_type}: {exec_result.error_signature.normalized_message}"
                        )
                    elif exec_result.timed_out:
                        errors_list.append(f"Execution timed out after {exec_result.duration_seconds:.1f}s")
                    elif exec_result.exit_code != 0:
                        errors_list.append(f"Process exited with code {exec_result.exit_code}")

                    if (
                        parsed.diagnose
                        and isinstance(exec_result.diagnosis, DiagnosisAbstention)
                    ):
                        debug_limitations.append(
                            f"Local SLM diagnosis abstained: {exec_result.diagnosis.details}"
                        )

                    debug_envelope = JsonEnvelope[ExecutionResult](
                        command="debug",
                        success=is_success,
                        target_path=target_record.path,
                        data=exec_result,
                        errors=errors_list,
                        limitations=debug_limitations,
                    )
                    write_json_envelope(debug_envelope, sys.stdout)
                else:
                    reporter = TerminalReporter(sys.stdout)
                    reporter.write(reporter.render_debug(target_record.path, exec_result))
                return EXIT_SUCCESS if is_success else EXIT_TARGET_FAILURE

            if parsed.command == "fix":
                fix_report = orchestrator.fix(
                    target=target_record,
                    apply=parsed.apply,
                    propose_only=parsed.propose_only,
                    expected_stdout=parsed.expected_stdout,
                    expected_stdout_contains=parsed.expected_stdout_contains,
                    expected_exit=parsed.expected_exit,
                    target_args=parsed.target_args,
                    stdin_file=parsed.stdin_file,
                    timeout=parsed.timeout,
                    fail_on_job_failure=parsed.fail_on_job_failure,
                )
                if parsed.json_output:
                    fix_limitations: list[str] = []
                    if fix_report.abstention is not None:
                        fix_limitations.append(
                            f"Fix workflow abstained: {fix_report.abstention.details}"
                        )
                    fix_envelope = JsonEnvelope[FixReport](
                        command="fix",
                        success=fix_report.applied
                        or (fix_report.proposal is not None and fix_report.abstention is None)
                        or ("No defect" in fix_report.message),
                        target_path=target_record.path,
                        data=fix_report,
                        errors=fix_report.abstention.validation_errors
                        if fix_report.abstention
                        else [],
                        limitations=fix_limitations,
                    )
                    write_json_envelope(fix_envelope, sys.stdout)
                else:
                    reporter = TerminalReporter(sys.stdout)
                    reporter.write(reporter.render_fix(target_record.path, fix_report))

                if fix_report.abstention is not None:
                    return EXIT_ABSTENTION
                if (
                    fix_report.applied
                    or fix_report.declined
                    or fix_report.proposal is not None
                    or ("No defect" in fix_report.message)
                ):
                    return EXIT_SUCCESS
                return EXIT_TARGET_FAILURE

            if parsed.command == "complexity":
                if parsed.selector:
                    report = orchestrator.analyze_complexity(
                        target=target_record,
                        selector=parsed.selector,
                    )
                    reports = [report]
                else:
                    reports = orchestrator.analyze_file_complexity(target=target_record)

                all_established = all(r.abstention_reason is None for r in reports)
                has_syntax_error = any(
                    r.abstention_reason == ComplexityAbstentionReason.UNSUPPORTED_SYNTAX
                    for r in reports
                )

                if parsed.json_output:
                    limitations = [
                        f"{r.target}: {r.details}"
                        for r in reports
                        if r.abstention_reason is not None
                    ]
                    json_data: ComplexityReport | list[ComplexityReport] = (
                        reports[0] if parsed.selector else reports
                    )
                    comp_envelope = JsonEnvelope[ComplexityReport | list[ComplexityReport]](
                        command="complexity",
                        success=all_established,
                        target_path=target_record.path
                        if not parsed.selector
                        else f"{target_record.path}::{parsed.selector}",
                        data=json_data,
                        errors=[
                            r.details
                            for r in reports
                            if r.abstention_reason == ComplexityAbstentionReason.UNSUPPORTED_SYNTAX
                        ],
                        limitations=limitations,
                    )
                    write_json_envelope(comp_envelope, sys.stdout)
                else:
                    reporter = TerminalReporter(sys.stdout)
                    disp_target = (
                        f"{target_record.path}::{parsed.selector}"
                        if parsed.selector
                        else target_record.path
                    )
                    reporter.write(reporter.render_complexity(reports, disp_target))

                if has_syntax_error:
                    return EXIT_TARGET_FAILURE
                if not all_established:
                    return EXIT_ABSTENTION
                return EXIT_SUCCESS

            if parsed.command == "profile":
                if not parsed.selector:
                    raise CliUsageError(
                        "Command 'profile' requires an explicit function or method selector "
                        "(e.g. 'script.py::function_name' or 'script.py::Class.method')."
                    )

                profile_report = orchestrator.profile(
                    target=target_record,
                    selector=parsed.selector,
                    input_file=parsed.input_file,
                    warmup_runs=parsed.warmup,
                    measured_runs=parsed.measured,
                    timeout=parsed.timeout,
                    fail_on_job_failure=parsed.fail_on_job_failure,
                )

                if parsed.json_output:
                    prof_envelope = JsonEnvelope[ProfileReport](
                        command="profile",
                        success=True,
                        target_path=f"{target_record.path}::{parsed.selector}",
                        data=profile_report,
                    )
                    write_json_envelope(prof_envelope, sys.stdout)
                else:
                    reporter = TerminalReporter(sys.stdout)
                    disp_target = f"{target_record.path}::{parsed.selector}"
                    reporter.write(
                        reporter.render_profile_report(
                            target=disp_target,
                            import_ms=profile_report.import_duration_ms,
                            latency_median_ms=profile_report.median_latency_ms,
                            latency_dispersion_ms=profile_report.dispersion_ms,
                            tracemalloc_bytes=profile_report.python_allocations_tracemalloc_bytes,
                            rss_bytes=profile_report.approximate_process_tree_rss_bytes,
                            warmup_runs=profile_report.warmup_invocations,
                            measured_runs=profile_report.measured_invocations,
                            hot_process=profile_report.hot_process_reused,
                        )
                    )
                return EXIT_SUCCESS

        return EXIT_SUCCESS

    except LocaldevError as exc:
        if is_json:
            cmd_name = parsed.command if parsed is not None else "unknown"
            target_name = parsed.target_file if parsed is not None else None
            err_envelope = JsonEnvelope[None](
                command=cmd_name,
                success=False,
                target_path=target_name,
                errors=[exc.message],
            )
            write_json_envelope(err_envelope, sys.stdout)
        else:
            sys.stderr.write(f"Error: {exc.message}\n")
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
