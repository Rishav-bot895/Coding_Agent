"""Bug sample 05: Invoking method on None value causing AttributeError."""


def find_item_title(item_id: int, database: dict[int, str]) -> str:
    """Find and uppercase item title from database lookup."""
    title = database.get(item_id)
    # Bug: title is None when item_id not found, calling strip() raises AttributeError
    return title.strip().upper()


if __name__ == "__main__":
    find_item_title(99, {1: "Widget", 2: "Gadget"})

