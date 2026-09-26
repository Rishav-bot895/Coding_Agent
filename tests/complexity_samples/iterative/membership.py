"""Fixtures demonstrating membership testing cost differences (Phase 10 Task P10-T2).

Verifies:
1. x in list -> O(n) worst-case sequence scan (O(n²) inside loop).
2. x in set -> O(1) expected time under uniform hashing (O(n) inside loop).
3. x in dict -> O(1) expected time under uniform hashing (O(n) inside loop).
4. Local set accumulator: O(n) auxiliary space.
5. Local list accumulator: O(n) auxiliary space.
"""

from __future__ import annotations


def membership_in_set(items: list[int], seen: set[int]) -> bool:
    """Set membership test is expected O(1), yielding O(n) total time."""
    for x in items:
        if x in seen:
            return True
    return False


def membership_in_local_set(items: list[int]) -> bool:
    """Local set membership and accumulation: O(n) expected time, O(n) auxiliary space."""
    seen = set()
    for x in items:
        if x in seen:
            return True
        seen.add(x)
    return False


def membership_in_list(items: list[int], target_list: list[int]) -> bool:
    """List membership test is O(n) sequence scan, yielding O(nm) or O(n²) total time."""
    for x in items:
        if x in target_list:
            return True
    return False


def membership_in_local_list(items: list[int]) -> bool:
    """Local list membership test scans in O(n), yielding O(n²) total time and O(n) aux space."""
    seen = []
    for x in items:
        if x in seen:
            return True
        seen.append(x)
    return False


def membership_in_dict(items: list[int], lookup: dict[int, str]) -> bool:
    """Dictionary key membership test is expected O(1), yielding O(n) total time."""
    for x in items:
        if x in lookup:
            return True
    return False

