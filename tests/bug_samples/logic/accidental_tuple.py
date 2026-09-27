"""Logic bug: trailing comma turns numeric score into single-element tuple."""


def get_base_score() -> int:
    # Bug: trailing comma produces tuple (100,)
    score = 100,
    return score  # type: ignore[return-value]


def main() -> None:
    score = get_base_score()
    assert isinstance(score, int), f"Expected int, got {type(score)}"


if __name__ == "__main__":
    main()
