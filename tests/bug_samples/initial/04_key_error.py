"""Bug sample 04: Accessing missing dictionary key causing KeyError."""


def get_user_role(profile: dict[str, str]) -> str:
    """Extract role from user profile dictionary."""
    # Bug: direct subscription without checking or get() fallback
    return profile["role"]


if __name__ == "__main__":
    get_user_role({"name": "Alice", "email": "alice@example.com"})

