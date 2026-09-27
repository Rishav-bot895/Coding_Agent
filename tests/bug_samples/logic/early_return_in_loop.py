"""Logic bug: return statement positioned inside loop terminates iteration prematurely."""


def all_positive(numbers: list[int]) -> bool:
    for n in numbers:
        if n > 0:  # noqa: SIM103
            return True  # Bug: returns True on first positive number, ignoring negatives later
        else:
            return False
    return True


def main() -> None:
    data = [5, -2, 10]
    # Should be False because -2 is not positive
    result = all_positive(data)
    assert result is False, f"Expected False for {data}, got {result}"


if __name__ == "__main__":
    main()
