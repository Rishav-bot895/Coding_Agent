"""Logic bug: variable name shadows builtin function name causing logic failure."""


def compute_metrics(values: list[int]) -> dict[str, int]:
    # Shadows builtin 'min'
    min = 0
    for v in values:
        if v < min:  # noqa: PLR1730
            min = v
    # Attempting to use builtin min with another sequence
    threshold = min  # Intent was to find min among values
    return {"minimum": threshold}


def main() -> None:
    vals = [10, 5, 20]
    res = compute_metrics(vals)
    # Bug: returned 0 instead of 5 because initial min was 0
    assert res["minimum"] == 5, f"Expected 5, got {res['minimum']}"


if __name__ == "__main__":
    main()
