"""Sample raising ImportError: missing attribute from module import."""

from math import non_existent_math_function_8888  # noqa: F401


def main() -> None:
    pass


if __name__ == "__main__":
    main()

