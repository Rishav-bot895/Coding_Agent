"""Additional algorithmic patterns for complexity analysis (Phase 12 evaluation).

Covers:
1. Linear while loop: O(n) time, O(1) aux space, O(1) output space.
2. Halving while loop: O(log n) time, O(1) aux space, O(1) output space.
3. Dictionary comprehension: O(n) time, O(1) aux space, O(n) output space.
4. Set comprehension: O(n) time, O(1) aux space, O(n) output space.
5. Nested matrix multiplication: abstention / higher order polynomial.
6. Dynamic break while loop: abstention.
7. Nested dynamic jump loop: abstention.
"""

from __future__ import annotations


def while_linear_decrement(n: int) -> int:
    """While loop with linear decrement: O(n) time, O(1) aux, O(1) out."""
    total = 0
    curr = n
    while curr > 0:
        total += curr
        curr -= 1
    return total


def while_log_halving(n: int) -> int:
    """While loop halving condition: O(log n) time, O(1) aux, O(1) out."""
    steps = 0
    curr = n
    while curr > 1:
        curr //= 2
        steps += 1
    return steps


def dict_comprehension_materialized(items: list[str]) -> dict[str, int]:
    """Dictionary comprehension: O(n) time, O(1) aux space, O(n) output space."""
    return {item: len(item) for item in items}


def set_comprehension_materialized(items: list[int]) -> set[int]:
    """Set comprehension: O(n) time, O(1) aux space, O(n) output space."""
    return {x * 2 for x in items}


def matrix_multiplication(a: list[list[int]], b: list[list[int]]) -> list[list[int]]:
    """Triple nested loop for matrix multiplication: O(n^3) / abstention."""
    n = len(a)
    result = [[0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            for k in range(n):
                result[i][j] += a[i][k] * b[k][j]
    return result


def dynamic_break_while(n: int, threshold: int) -> int:
    """Dynamic break condition resulting in indeterminate iterations -> abstention."""
    count = 0
    i = 0
    while i < n:
        if (i * 7 + 3) % 11 == threshold:
            break
        count += 1
        i += 1
    return count

