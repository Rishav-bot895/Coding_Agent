"""Pure deterministic calculation targets for profiling evaluation."""

from __future__ import annotations

import hashlib


def hash_string(text: str) -> str:
    """Compute SHA-256 hex digest of string input."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sum_primes(limit: int) -> int:
    """Compute sum of all prime numbers up to limit."""
    if limit < 2:
        return 0
    sieve = [True] * (limit + 1)
    sieve[0] = sieve[1] = False
    p = 2
    while p * p <= limit:
        if sieve[p]:
            for i in range(p * p, limit + 1, p):
                sieve[i] = False
        p += 1
    return sum(i for i, is_p in enumerate(sieve) if is_p)

