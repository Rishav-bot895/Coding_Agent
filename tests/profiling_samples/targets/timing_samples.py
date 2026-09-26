"""Timing test targets for localdev profiling tests."""

import time


def sleep_fifteen_ms() -> None:
    """Sleep for 15 milliseconds."""
    time.sleep(0.015)


def compute_squares(limit: int = 1000) -> int:
    """Compute sum of squares up to limit."""
    return sum(i * i for i in range(limit))


def quick_add(a: int, b: int) -> int:
    """Trivial addition."""
    return a + b
