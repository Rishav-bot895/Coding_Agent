"""Mutating function targets for profiling evaluation."""

from __future__ import annotations


def in_place_sort(items: list[int]) -> list[int]:
    """Mutate list in place with sort()."""
    items.sort()
    return items


def pop_all(items: list[int]) -> int:
    """Mutate list by popping all elements."""
    count = 0
    while items:
        items.pop()
        count += 1
    return count


def mutate_dict(data: dict[str, int]) -> dict[str, int]:
    """Mutate dictionary in place."""
    data["mutated"] = 999
    return data

