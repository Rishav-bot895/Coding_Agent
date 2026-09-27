"""Logic bug: loop bounds skip the final element (off-by-one)."""


def calculate_sum(items: list[int]) -> int:
    total = 0
    # Bug: range(len(items) - 1) skips the last element
    for i in range(len(items) - 1):
        total += items[i]
    return total


def main() -> None:
    data = [10, 20, 30]
    expected = 60
    actual = calculate_sum(data)
    assert actual == expected, f"Expected {expected}, got {actual}"


if __name__ == "__main__":
    main()

