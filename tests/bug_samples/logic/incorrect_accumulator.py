"""Logic bug: multiplying accumulator initialized to 0 instead of 1."""


def compute_product(numbers: list[int]) -> int:
    # Bug: initialized to 0; any multiplication will remain 0
    product = 0
    for n in numbers:
        product *= n
    return product


def main() -> None:
    nums = [2, 3, 4]
    expected = 24
    actual = compute_product(nums)
    assert actual == expected, f"Expected {expected}, got {actual}"


if __name__ == "__main__":
    main()

