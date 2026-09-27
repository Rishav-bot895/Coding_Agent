"""Sample attempting relative import outside package context."""

from ..unbound_parent_module import something_important  # noqa: F401


def main() -> None:
    pass


if __name__ == "__main__":
    main()

