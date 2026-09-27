"""Sample raising ValueError: not enough values to unpack."""


def main() -> None:
    items = [1, 2]
    _first, _second, _third = items  # type: ignore[misc]


if __name__ == "__main__":
    main()

