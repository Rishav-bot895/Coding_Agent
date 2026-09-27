"""Sample raising AttributeError: 'NoneType' object has no attribute 'strip'."""


def main() -> None:
    text: str | None = None
    _ = text.strip()  # type: ignore[union-attr]


if __name__ == "__main__":
    main()

