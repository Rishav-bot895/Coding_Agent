"""Sample raising UnboundLocalError: local variable referenced before assignment."""

counter = 10


def increment() -> None:
    counter += 1  # noqa: F823, F841


if __name__ == "__main__":
    increment()

