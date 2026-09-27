"""Sample accessing nonexistent attribute on standard library module."""

import sys


def main() -> None:
    _ = sys.nonexistent_kernel_symbol_xyz  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()

