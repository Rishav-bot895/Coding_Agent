"""Circular import fixture B: imports A while A imports B."""

from . import circular_import_a  # noqa: F401


def get_value_b() -> int:
    return 200


if __name__ == "__main__":
    pass

