"""Sample raising TypeError: unsupported operand type(s) for +: 'int' and 'str'."""


def main() -> None:
    count = 10
    label = "items"
    _ = count + label  # type: ignore[operator]


if __name__ == "__main__":
    main()

