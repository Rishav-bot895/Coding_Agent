"""Logic bug: using 'is' identity operator for value equality on dynamic integers."""


def check_threshold(computed_val: int, expected: int) -> bool:
    # Bug: 'is' checks object identity rather than numeric value equality
    return computed_val is expected


def main() -> None:
    # Large integers created via computation are distinct objects in memory
    val = 1000 + 42
    target = 1042
    assert check_threshold(val, target) is True, "Calculated value should match target threshold"


if __name__ == "__main__":
    main()
