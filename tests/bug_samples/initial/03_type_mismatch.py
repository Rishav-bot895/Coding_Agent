"""Bug sample 03: Incompatible operand types causing TypeError."""


def format_user_id(prefix: str, user_id: int) -> str:
    """Format user identifier with given prefix."""
    # Bug: cannot concatenate str and int directly with + operator
    return prefix + user_id


if __name__ == "__main__":
    format_user_id("USER_", 1042)

