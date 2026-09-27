"""Sample raising IndexError: pop from empty list."""


def main() -> None:
    items: list[int] = []
    items.pop()


if __name__ == "__main__":
    main()

