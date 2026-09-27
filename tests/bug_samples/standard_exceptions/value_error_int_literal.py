"""Sample raising ValueError: invalid literal for int() with base 10."""


def main() -> None:
    raw = "not_a_number_123"
    _ = int(raw)


if __name__ == "__main__":
    main()

