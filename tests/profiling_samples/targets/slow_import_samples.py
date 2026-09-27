"""Slow import module target to profile import-time overhead."""

import time

# Simulate non-trivial import-time initialization overhead
time.sleep(0.05)


def fast_add(a: int, b: int) -> int:
    """Fast function after slow import."""
    return a + b

