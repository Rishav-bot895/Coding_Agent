"""Stateful test targets for verifying hot-process persistence semantics."""

from functools import lru_cache

CALL_HISTORY: list[int] = []
CACHE_CALLS: int = 0


def record_history(item: int) -> int:
    """Append item to global list and return its accumulated length."""
    CALL_HISTORY.append(item)
    return len(CALL_HISTORY)


def get_history_length() -> int:
    """Return accumulated length of global history."""
    return len(CALL_HISTORY)


@lru_cache(maxsize=64)
def cached_heavy_computation(base: int, exp: int) -> int:
    """Pure computation with LRU caching to measure hot cache speedup."""
    global CACHE_CALLS
    CACHE_CALLS += 1
    return base ** exp


def get_cache_call_count() -> int:
    """Return number of underlying cache misses."""
    return CACHE_CALLS
