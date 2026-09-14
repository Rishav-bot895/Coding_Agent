"""Bug sample 01: Off-by-one loop bound causing IndexError."""


def calculate_prefix_sums(numbers: list[int]) -> list[int]:
    """Calculate running prefix sums of a list of numbers."""
    result: list[int] = []
    current = 0
    # Bug: range goes up to len(numbers) + 1, causing IndexError on numbers[i]
    for i in range(len(numbers) + 1):
        current += numbers[i]
        result.append(current)
    return result


if __name__ == "__main__":
    calculate_prefix_sums([10, 20, 30])

