"""Stable CLI exit codes and helper utilities for localdev.

Provides enumeration and descriptive metadata for all defined process exit codes
across deterministic and model-assisted operations.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Final

from localdev.constants import (
    EXIT_ABSTENTION,
    EXIT_CLI_USAGE_ERROR,
    EXIT_INFERENCE_ERROR,
    EXIT_SUCCESS,
    EXIT_TARGET_FAILURE,
    EXIT_TARGET_IO_ERROR,
    EXIT_TIMEOUT_RESOURCE_BREACH,
)


class ExitCode(IntEnum):
    """Enumeration of stable exit codes for localdev."""

    SUCCESS = EXIT_SUCCESS
    TARGET_FAILURE = EXIT_TARGET_FAILURE
    CLI_USAGE_ERROR = EXIT_CLI_USAGE_ERROR
    TARGET_IO_ERROR = EXIT_TARGET_IO_ERROR
    TIMEOUT_RESOURCE_BREACH = EXIT_TIMEOUT_RESOURCE_BREACH
    INFERENCE_ERROR = EXIT_INFERENCE_ERROR
    ABSTENTION = EXIT_ABSTENTION


EXIT_CODE_DESCRIPTIONS: Final[dict[int, str]] = {
    EXIT_SUCCESS: "Command completed successfully.",
    EXIT_TARGET_FAILURE: "Target script failed or bug was diagnosed.",
    EXIT_CLI_USAGE_ERROR: "CLI usage or argument parsing error.",
    EXIT_TARGET_IO_ERROR: "Target file validation or I/O error.",
    EXIT_TIMEOUT_RESOURCE_BREACH: "Subprocess execution exceeded timeout or output byte cap.",
    EXIT_INFERENCE_ERROR: "Local SLM inference or schema generation error.",
    EXIT_ABSTENTION: "Analysis safely abstained due to unestablished semantics.",
}


def get_exit_code_name(code: int) -> str:
    """Return symbolic name of exit code, or 'UNKNOWN'."""
    try:
        return ExitCode(code).name
    except ValueError:
        return "UNKNOWN"


def get_exit_code_description(code: int) -> str:
    """Return descriptive explanation of exit code."""
    return EXIT_CODE_DESCRIPTIONS.get(code, f"Unrecognized exit code ({code}).")


def is_success(code: int) -> bool:
    """Return True if exit code indicates successful execution."""
    return code == EXIT_SUCCESS

