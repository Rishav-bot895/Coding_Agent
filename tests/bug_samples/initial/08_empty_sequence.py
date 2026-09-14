"""Bug sample 08: Accessing element of empty sequence causing IndexError."""


def get_first_element(items: list[int]) -> int:
    """Retrieve first item from list without emptiness check."""
    # Bug: indexing items[0] on empty list raises IndexError
    return items[0]


if __name__ == "__main__":
    get_first_element([])

