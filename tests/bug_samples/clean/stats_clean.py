"""Clean sample: basic statistical summary calculation."""

import math


def compute_statistics(values: list[float]) -> dict[str, float]:
    """Calculate count, mean, and sample standard deviation."""
    if not values:
        raise ValueError("Cannot calculate statistics on empty list")
    n = len(values)
    mean = sum(values) / n
    if n > 1:
        variance = sum((x - mean) ** 2 for x in values) / (n - 1)
        stdev = math.sqrt(variance)
    else:
        stdev = 0.0
    return {"count": float(n), "mean": mean, "stdev": stdev}


def main() -> None:
    data = [10.0, 20.0, 30.0]
    stats = compute_statistics(data)
    assert stats["count"] == 3.0
    assert stats["mean"] == 20.0
    assert math.isclose(stats["stdev"], 10.0)


if __name__ == "__main__":
    main()

