"""Clean sample: deterministic quicksort implementation."""


def quicksort(items: list[int]) -> list[int]:
    """Return a new sorted list using quicksort."""
    if len(items) <= 1:
        return list(items)
    pivot = items[len(items) // 2]
    left = [x for x in items if x < pivot]
    middle = [x for x in items if x == pivot]
    right = [x for x in items if x > pivot]
    return quicksort(left) + middle + quicksort(right)


def main() -> None:
    data = [64, 34, 25, 12, 22, 11, 90]
    sorted_data = quicksort(data)
    assert sorted_data == [11, 12, 22, 25, 34, 64, 90]


if __name__ == "__main__":
    main()

