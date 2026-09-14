"""Bug sample 10: Invalid string literal in int conversion causing ValueError."""


def parse_port_number(port_str: str) -> int:
    """Parse port number string to integer."""
    # Bug: parsing non-numeric strings raises ValueError
    return int(port_str)


if __name__ == "__main__":
    parse_port_number("invalid_port")

