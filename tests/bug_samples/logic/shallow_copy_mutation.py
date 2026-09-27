"""Logic bug: modifying nested elements of a shallow list copy."""


def clone_and_modify(grid: list[list[int]], row: int, col: int, val: int) -> list[list[int]]:
    # Bug: shallow copy shares inner lists
    new_grid = list(grid)
    new_grid[row][col] = val
    return new_grid


def main() -> None:
    original = [[1, 2], [3, 4]]
    _modified = clone_and_modify(original, 0, 0, 99)
    # Original grid should remain unmodified
    assert original[0][0] == 1, f"Expected 1, got {original[0][0]} due to shallow copy mutation"


if __name__ == "__main__":
    main()

