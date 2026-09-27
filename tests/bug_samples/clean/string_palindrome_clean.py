"""Clean sample: string palindrome validator."""


def is_palindrome(text: str) -> bool:
    """Return True if text is a palindrome ignoring case and non-alphanumeric chars."""
    cleaned = [ch.lower() for ch in text if ch.isalnum()]
    return cleaned == cleaned[::-1]


def main() -> None:
    assert is_palindrome("A man, a plan, a canal: Panama") is True
    assert is_palindrome("race a car") is False
    assert is_palindrome("") is True


if __name__ == "__main__":
    main()

