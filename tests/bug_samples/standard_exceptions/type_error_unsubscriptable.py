"""Sample raising TypeError: 'NoneType' object is not subscriptable."""


def main() -> None:
    data = None
    _ = data[0]  # type: ignore[index]


if __name__ == "__main__":
    main()

