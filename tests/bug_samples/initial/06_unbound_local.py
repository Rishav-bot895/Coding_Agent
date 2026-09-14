# ruff: noqa
"""Bug sample 06: Local variable referenced before assignment causing UnboundLocalError."""

counter = 10


def increment_counter() -> int:
    """Attempt to increment global counter without global keyword."""
    # Bug: counter is assigned later in scope, making it a local variable here
    counter += 1
    return counter


if __name__ == "__main__":
    increment_counter()
