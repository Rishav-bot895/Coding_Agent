"""Logic bug: mutable default list argument shared across invocations."""


def collect_items(item: str, container: list[str] = []) -> list[str]:  # noqa: B006
    container.append(item)
    return container


def main() -> None:
    _first = collect_items("apple")
    second = collect_items("banana")
    # Bug: second contains ['apple', 'banana'] instead of ['banana']
    assert len(second) == 1, f"Expected 1 item, got {len(second)}"


if __name__ == "__main__":
    main()
