"""Fixtures demonstrating auxiliary space vs output space separation (Phase 10 Task P10-T2).

Verifies:
1. Returned list comprehension: aux O(1), output O(n).
2. Temporary list comprehension consumed internally: aux O(n), output O(1).
3. Generator expression consumed in loop: aux O(1), output O(1).
4. Returned generator expression: aux O(1), output O(1).
5. Generator function with yield: aux O(1), output O(1).
6. Temporary slicing: aux O(n), output O(1).
7. Returned slicing: aux O(1), output O(n).
"""

from __future__ import annotations


def returned_list_comprehension(items: list[int]) -> list[int]:
    """Returned list comprehension materializes elements in output space."""
    return [x * 2 for x in items]


def temporary_list_comprehension_consumed(items: list[int]) -> int:
    """Temporary list comprehension allocates O(n) auxiliary heap space."""
    for x in [item * 2 for item in items]:
        pass
    return 0


def generator_expression_consumed(items: list[int]) -> int:
    """Generator expression consumed lazily in O(1) auxiliary space."""
    for x in (item * 2 for item in items):
        pass
    return 0


def returned_generator_expression(items: list[int]):
    """Returned generator expression provides lazy iterator in O(1) output space."""
    return (x * 2 for x in items)


def generator_function_yield(items: list[int]):
    """Generator function with yield returns lazy iterator in O(1) output space."""
    for x in items:
        yield x * 2


def temporary_slice(items: list[int]) -> int:
    """Intermediate slicing creates an O(n) auxiliary copy."""
    sub = items[1:]
    return len(sub)


def returned_slice(items: list[int]) -> list[int]:
    """Returned slice creates an O(n) copy in output space."""
    return items[1:]

