"""Sample raising RuntimeError at import time through dependent module."""

from . import broken_helper_runtime  # noqa: F401


def main() -> None:
    pass


if __name__ == "__main__":
    main()

