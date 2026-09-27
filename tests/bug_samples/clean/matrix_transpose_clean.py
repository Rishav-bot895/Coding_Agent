"""Clean sample: 2D Matrix transpose."""


def transpose(matrix: list[list[int]]) -> list[list[int]]:
    """Return transpose of a 2D matrix."""
    if not matrix or not matrix[0]:
        return []
    rows = len(matrix)
    cols = len(matrix[0])
    return [[matrix[r][c] for r in range(rows)] for c in range(cols)]


def main() -> None:
    mat = [
        [1, 2, 3],
        [4, 5, 6],
    ]
    transposed = transpose(mat)
    assert transposed == [
        [1, 4],
        [2, 5],
        [3, 6],
    ]


if __name__ == "__main__":
    main()

