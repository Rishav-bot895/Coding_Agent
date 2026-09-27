"""Sample raising NameError due to misspelled variable name."""


def main() -> None:
    total_score = 100
    _final_score = total_score + bonus_points  # noqa: F821


if __name__ == "__main__":
    main()

