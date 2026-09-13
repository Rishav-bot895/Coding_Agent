"""Strongly typed exception hierarchy for localdev.

Every exception class maps directly to a defined stable CLI exit code, ensuring
predictable exit semantics across all deterministic and model-assisted workflows.
"""

from __future__ import annotations

from typing import Any

from localdev.constants import (
    EXIT_ABSTENTION,
    EXIT_CLI_USAGE_ERROR,
    EXIT_INFERENCE_ERROR,
    EXIT_TARGET_FAILURE,
    EXIT_TARGET_IO_ERROR,
    EXIT_TIMEOUT_RESOURCE_BREACH,
)


class LocaldevError(Exception):
    """Base exception for all localdev operational and application errors."""

    def __init__(self, message: str, exit_code: int = EXIT_TARGET_FAILURE) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code

    def __str__(self) -> str:
        return self.message


class TargetValidationError(LocaldevError):
    """Raised when target file validation fails (missing, directory, oversized, symlink)."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message, exit_code=EXIT_TARGET_IO_ERROR)
        self.details = details or {}


class StaleEditError(TargetValidationError):
    """Raised when SHA-256 compare-before-replace detects external file modification."""

    def __init__(self, message: str, expected_hash: str, actual_hash: str) -> None:
        super().__init__(
            message,
            details={"expected_hash": expected_hash, "actual_hash": actual_hash},
        )
        self.expected_hash = expected_hash
        self.actual_hash = actual_hash


class CliUsageError(LocaldevError):
    """Raised for CLI invocation and argument parsing misuse."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=EXIT_CLI_USAGE_ERROR)


class ResourceBreachError(LocaldevError):
    """Raised when execution exceeds wall-clock timeout or output byte cap."""

    def __init__(self, message: str, breach_type: str) -> None:
        super().__init__(message, exit_code=EXIT_TIMEOUT_RESOURCE_BREACH)
        self.breach_type = breach_type


class ExecutionTimeoutError(ResourceBreachError):
    """Raised when subprocess execution exceeds the configured wall-clock timeout."""

    def __init__(self, message: str, timeout_seconds: float) -> None:
        super().__init__(message, breach_type="timeout")
        self.timeout_seconds = timeout_seconds


class OutputCapExceededError(ResourceBreachError):
    """Raised when combined subprocess stdout/stderr exceeds configured byte cap."""

    def __init__(self, message: str, byte_cap: int) -> None:
        super().__init__(message, breach_type="output_cap")
        self.byte_cap = byte_cap


class InferenceError(LocaldevError):
    """Raised when local SLM inference fails (Ollama unreachable, timeout, or bad status)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message, exit_code=EXIT_INFERENCE_ERROR)
        self.status_code = status_code


class SchemaValidationError(InferenceError):
    """Raised when model JSON output fails strict Pydantic schema validation."""

    def __init__(self, message: str, raw_payload: str | None = None) -> None:
        super().__init__(message)
        self.raw_payload = raw_payload


class AbstentionError(LocaldevError):
    """Raised when analysis safely abstains due to unestablished static/dynamic semantics."""

    def __init__(self, message: str, reason_code: str) -> None:
        super().__init__(message, exit_code=EXIT_ABSTENTION)
        self.reason_code = reason_code


class PatchApplicationError(LocaldevError):
    """Raised when applying an edit proposal to candidate copy or disk fails."""

    def __init__(self, message: str, edit_index: int | None = None) -> None:
        super().__init__(message, exit_code=EXIT_TARGET_FAILURE)
        self.edit_index = edit_index

