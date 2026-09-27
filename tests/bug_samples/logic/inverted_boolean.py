"""Logic bug: inverted boolean condition in authentication check."""


def is_authorized(age: int, has_token: bool) -> bool:
    # Bug: requires NOT having token instead of having token
    return age >= 18 and not has_token


def main() -> None:
    allowed = is_authorized(25, True)
    assert allowed is True, "Valid adult with token should be authorized"


if __name__ == "__main__":
    main()

