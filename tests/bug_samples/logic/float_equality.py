"""Logic bug: exact equality comparison on floating point calculations."""


def is_sum_equal(a: float, b: float, target: float) -> bool:
    # Bug: direct floating point equality without math.isclose()
    return (a + b) == target


def main() -> None:
    # 0.1 + 0.2 != 0.3 in IEEE-754 binary floating point
    result = is_sum_equal(0.1, 0.2, 0.3)
    assert result is True, "0.1 + 0.2 should equal 0.3 within numerical tolerance"


if __name__ == "__main__":
    main()

