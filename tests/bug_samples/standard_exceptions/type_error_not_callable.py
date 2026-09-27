"""Sample raising TypeError: 'int' object is not callable."""


def main() -> None:
    val = 42
    val()  # type: ignore[operator]


if __name__ == "__main__":
    main()

