"""Failing test targets for verifying error handling in profiling worker."""


def raise_zero_division() -> float:
    """Intentionally raise ZeroDivisionError."""
    return 10.0 / 0.0


def raise_value_error(msg: str = "bad argument") -> None:
    """Raise ValueError with custom message."""
    raise ValueError(msg)
