"""Command-line interface entry point for localdev.

Provides command parsing, help text, and dispatch for native Windows single-file
offline Python analysis, debugging, fixing, complexity estimation, and profiling.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from localdev.constants import (
    APP_NAME,
    APP_VERSION,
    EXIT_CLI_USAGE_ERROR,
    EXIT_SUCCESS,
)


def create_parser() -> argparse.ArgumentParser:
    """Construct the primary top-level CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description=(
            "localdev: Native Windows single-file offline Python coding agent.\n"
            "Personal portfolio project for user-owned or trusted code only.\n"
            "Supported platform: Windows 11 x64 only. Operational limits terminate\n"
            "runaways but do NOT constitute a security sandbox."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"{APP_NAME} {APP_VERSION}",
        help="Show application version and exit.",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured RFC 8259 JSON envelope instead of human-readable text.",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        description="Available single-target inspection, analysis, and repair commands",
        metavar="<command>",
    )

    # info
    p_info = subparsers.add_parser(
        "info",
        help="Inspect single target file metadata, encoding, BOM, and line endings.",
    )
    p_info.add_argument("target", help="Explicit target Python source file.")

    # detect
    p_detect = subparsers.add_parser(
        "detect",
        help="Perform non-executing language detection (extension, shebang, AST).",
    )
    p_detect.add_argument("target", help="Explicit target file to classify.")

    # analyse
    p_analyse = subparsers.add_parser(
        "analyse",
        help="Run static syntax check, AST fact extraction, and isolated Ruff linting.",
    )
    p_analyse.add_argument("target", help="Explicit target Python source file.")

    # debug
    p_debug = subparsers.add_parser(
        "debug",
        help="Execute target in controlled runtime (-E -B -P) and parse tracebacks.",
    )
    p_debug.add_argument("target", help="Explicit target Python source file.")
    p_debug.add_argument(
        "--stdin-file",
        help="Path to file supplying stdin for execution.",
    )
    p_debug.add_argument(
        "target_args",
        nargs="*",
        help="Optional arguments passed to target script after --.",
    )

    # fix
    p_fix = subparsers.add_parser(
        "fix",
        help="Diagnose failure with local SLM and propose guarded structured patch.",
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
        "--expected-exit",
        type=int,
        help="Expected exit code for Level D behavioral validation.",
    )

    # complexity
    p_complexity = subparsers.add_parser(
        "complexity",
        help="Perform static time, auxiliary-space, and output-space analysis.",
    )
    p_complexity.add_argument(
        "target",
        help="Explicit target Python source file (or file.py::func selector).",
    )

    # profile
    p_profile = subparsers.add_parser(
        "profile",
        help="Profile import cost, invocation latency, tracemalloc allocations, and RSS.",
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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI console script entry point for localdev."""
    parser = create_parser()

    if argv is None:
        argv = sys.argv[1:]

    if not argv:
        parser.print_help(sys.stderr)
        return EXIT_CLI_USAGE_ERROR

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help(sys.stderr)
        return EXIT_CLI_USAGE_ERROR

    # Command logic will be wired in subsequent tasks (P2-T1 through P11-T4)
    sys.stdout.write(f"localdev {args.command}: initialized for target '{getattr(args, 'target', '')}'.\n")
    return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())

