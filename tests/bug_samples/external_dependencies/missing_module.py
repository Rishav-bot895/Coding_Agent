"""Sample raising ModuleNotFoundError: missing external module."""

import non_existent_external_module_99999  # noqa: F401


def main() -> None:
    pass


if __name__ == "__main__":
    main()

