"""Boundary fixture: read-only target file permissions."""


def protected_read_action() -> str:
    return "read_only_access"


if __name__ == "__main__":
    print(protected_read_action())

