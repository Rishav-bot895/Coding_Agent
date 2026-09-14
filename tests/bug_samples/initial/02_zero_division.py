"""Bug sample 02: Unhandled division by zero in average calculation."""


def compute_average(total: float, count: int) -> float:
    """Compute average given a total sum and item count."""
    # Bug: no check for count == 0 before division
    return total / count


if __name__ == "__main__":
    compute_average(100.0, 0)

