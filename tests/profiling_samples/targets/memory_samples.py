"""Memory allocation test targets for verifying tracemalloc tracking."""


def allocate_two_mb() -> int:
    """Allocate 2 MB bytearray on the Python heap."""
    buf = bytearray(2 * 1024 * 1024)
    return len(buf)


def allocate_five_mb() -> int:
    """Allocate 5 MB bytearray on the Python heap."""
    buf = bytearray(5 * 1024 * 1024)
    return len(buf)


def minimal_allocation() -> int:
    """Near-zero heap allocation function."""
    return 42
