"""Clean sample: iterative binary search algorithm."""


def binary_search(arr: list[int], target: int) -> int:
    """Return index of target in sorted array, or -1 if not found."""
    low = 0
    high = len(arr) - 1
    while low <= high:
        mid = (low + high) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1


def main() -> None:
    data = [2, 5, 8, 12, 16, 23, 38, 56, 72, 91]
    assert binary_search(data, 23) == 5
    assert binary_search(data, 99) == -1


if __name__ == "__main__":
    main()

