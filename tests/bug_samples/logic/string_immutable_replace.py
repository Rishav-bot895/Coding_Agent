"""Logic bug: ignoring string.replace() return value."""


def sanitize_message(msg: str) -> str:
    # Bug: str.replace returns a new string; original remains unchanged
    msg.replace("bad_word", "***")
    return msg


def main() -> None:
    raw = "hello bad_word world"
    sanitized = sanitize_message(raw)
    assert "***" in sanitized, f"Expected sanitization, but got: {sanitized}"


if __name__ == "__main__":
    main()

