"""Recursive algorithm fixtures for complexity analysis (Phase 10 Task P10-T3).

Covers:
1. Direct linear recursion: O(n) time, O(n) auxiliary call-stack space.
2. Divide-and-conquer recursion: O(log n) time, O(log n) auxiliary space.
3. Binary branching recursion: O(2^n) time, O(n) auxiliary space.
4. Missing static base case: abstains with DYNAMIC_RECURSION.
5. Mutual recursion: abstains with DYNAMIC_RECURSION.
6. Dynamic step recursion: abstains with DYNAMIC_RECURSION.
7. Dynamic branching loop recursion: abstains with DYNAMIC_RECURSION.
"""

from __future__ import annotations


def factorial(n: int) -> int:
    """Direct linear recursion with integer decrement: O(n) time and O(n) aux space."""
    if n <= 1:
        return 1
    return n * factorial(n - 1)


def linear_traversal(items: list[int]) -> int:
    """Direct linear recursion with sequence slicing: O(n) time and O(n) aux space."""
    if not items:
        return 0
    return items[0] + linear_traversal(items[1:])


def halving_rec(n: int) -> int:
    """Fixed divide-and-conquer halving: O(log n) time and O(log n) aux space."""
    if n <= 1:
        return 1
    return 1 + halving_rec(n // 2)


def binary_search_rec(arr: list[int], target: int, low: int, high: int) -> int:
    """Binary search divide-and-conquer: O(log n) time and O(log n) aux space."""
    if low > high:
        return -1
    mid = (low + high) // 2
    if arr[mid] == target:
        return mid
    if arr[mid] > target:
        return binary_search_rec(arr, target, low, mid - 1)
    return binary_search_rec(arr, target, mid + 1, high)


def fibonacci_naive(n: int) -> int:
    """Binary branching recursion without memoization: O(2^n) time and O(n) aux space."""
    if n <= 1:
        return n
    return fibonacci_naive(n - 1) + fibonacci_naive(n - 2)


def missing_base_case(n: int) -> int:
    """Recursion lacking a recognizable static base case -> DYNAMIC_RECURSION."""
    return n * missing_base_case(n - 1)


def mutual_even(n: int) -> bool:
    """Mutual recursion with mutual_odd -> DYNAMIC_RECURSION."""
    if n == 0:
        return True
    return mutual_odd(n - 1)


def mutual_odd(n: int) -> bool:
    """Mutual recursion with mutual_even -> DYNAMIC_RECURSION."""
    if n == 0:
        return False
    return mutual_even(n - 1)


def dynamic_collatz(n: int) -> int:
    """Data-dependent recurrence step -> DYNAMIC_RECURSION."""
    if n <= 1:
        return 0
    if n % 2 == 0:
        return 1 + dynamic_collatz(n // 2)
    return 1 + dynamic_collatz(3 * n + 1)


def loop_recursion(items: list[int]) -> int:
    """Recursive call inside loop (dynamic branching) -> DYNAMIC_RECURSION."""
    if not items:
        return 0
    total = 0
    for _ in items:
        total += loop_recursion(items[1:])
    return total
