"""Iterative algorithmic fixtures for complexity analysis (Phase 10 Task P10-T2).

Covers all 5 supported iterative complexity classes:
- O(1)
- O(n)
- O(n log n)
- O(n²)
- O(nm)
and O(n³) and out-of-vocabulary combinations.
"""

from __future__ import annotations


def constant_operations(x: int, y: int) -> int:
    """O(1) time, O(1) auxiliary space, O(1) output space."""
    a = x + y
    b = a * 3
    return b - 5


def constant_range_loop(x: int) -> int:
    """O(1) time with constant loop bounds."""
    total = x
    for i in range(10):
        total += i
    return total


def single_linear_loop(items: list[int]) -> int:
    """O(n) time, O(1) auxiliary space, O(1) output space."""
    total = 0
    for x in items:
        total += x
    return total


def sequential_loops(items: list[int]) -> int:
    """Sequential loop addition: O(n) + O(n) = O(n) time."""
    count1 = 0
    for x in items:
        count1 += 1
    count2 = 0
    for y in items:
        count2 += 1
    return count1 + count2


def append_loop(items: list[int]) -> list[int]:
    """O(n) time with amortized O(1) append, O(n) output space."""
    res = []
    for x in items:
        res.append(x * 2)
    return res


def builtin_sorted(items: list[int]) -> list[int]:
    """O(n log n) time and O(n) auxiliary run buffers via sorted()."""
    return sorted(items)


def in_place_sort(items: list[int]) -> None:
    """O(n log n) time in-place sort with O(n) auxiliary run buffers."""
    items.sort()


def nested_same_dimension(items: list[int]) -> int:
    """Pairwise nested loops on same dimension: O(n) * O(n) = O(n²)."""
    count = 0
    for x in items:
        for y in items:
            count += 1
    return count


def nested_distinct_dimensions(items1: list[int], items2: list[int]) -> int:
    """Nested loops on distinct dimensions: O(n) * O(m) = O(nm)."""
    count = 0
    for x in items1:
        for y in items2:
            count += 1
    return count


def triple_nested_loop(items: list[int]) -> int:
    """Triple nested loop on same dimension: O(n) * O(n) * O(n) = O(n³)."""
    total = 0
    for x in items:
        for y in items:
            for z in items:
                total += 1
    return total


def four_nested_loops(items: list[int]) -> int:
    """Four nested loops exceed closed vocabulary maximum O(n³) -> UNKNOWN."""
    total = 0
    for a in items:
        for b in items:
            for c in items:
                for d in items:
                    total += 1
    return total

