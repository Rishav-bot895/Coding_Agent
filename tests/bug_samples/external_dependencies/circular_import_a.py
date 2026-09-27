"""Circular import fixture A: imports B while B imports A."""

from . import circular_import_b  # noqa: F401


def get_value_a() -> int:
    return 100


if __name__ == "__main__":
    pass

