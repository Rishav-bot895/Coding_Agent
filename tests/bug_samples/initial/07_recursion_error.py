"""Bug sample 07: Missing termination condition causing RecursionError."""


def countdown(n: int) -> list[int]:
    """Build countdown sequence recursively."""
    # Bug: missing base condition (e.g. if n <= 0: return [0]), recurses indefinitely
    return [n] + countdown(n - 1)


if __name__ == "__main__":
    countdown(5)

