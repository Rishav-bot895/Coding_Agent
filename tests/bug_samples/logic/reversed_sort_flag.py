"""Logic bug: sorting with incorrect reverse flag when descending order requested."""


def get_top_three_scores(scores: list[int]) -> list[int]:
    # Bug: reverse=False produces ascending order; highest scores should be first
    sorted_scores = sorted(scores, reverse=False)
    return sorted_scores[:3]


def main() -> None:
    scores = [10, 95, 45, 80, 20]
    top = get_top_three_scores(scores)
    # Intended top 3: [95, 80, 45]
    assert top[0] == 95, f"Expected highest score first, got {top}"


if __name__ == "__main__":
    main()

