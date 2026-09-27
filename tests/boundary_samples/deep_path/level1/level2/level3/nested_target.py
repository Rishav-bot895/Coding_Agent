"""Boundary fixture: deeply nested file path resolution."""


def nested_action() -> str:
    return "deeply_nested_success"


if __name__ == "__main__":
    print(nested_action())

