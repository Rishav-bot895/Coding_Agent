"""Fixture with comprehensions, loops, branches, and nested functions."""


def outer_processor(items: list[int]) -> list[int]:
    """Function containing a nested helper and comprehensions."""

    def inner_helper(val: int) -> int:
        return val * 2

    # Comprehensions
    squares = [inner_helper(x) for x in items if x > 0]
    unique_set = {x for x in squares}
    mapping = {x: str(x) for x in unique_set}
    gen = (x for x in mapping)

    # Loops and branches
    total = 0
    for key in gen:
        while total < 10:
            if total % 2 == 0:
                total += int(key)
            else:
                total += 1
            break

    try:
        if total > 5:
            return list(mapping.keys())
        return [inner_helper(total)]
    except (ValueError, TypeError, KeyError):
        return []


def recursive_factorial(n: int) -> int:
    """Directly recursive factorial function."""
    if n <= 1:
        return 1
    return n * recursive_factorial(n - 1)
