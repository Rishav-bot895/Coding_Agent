"""Fixture with standard functions and classes."""


def standalone_func(x: int, y: int = 10, *args: int, z: int = 20, **kwargs: str) -> int:
    """A standalone function with comprehensive parameters."""
    return x + y + sum(args) + z


def simple_utility() -> str:
    """Simple utility with no parameters."""
    return "ok"


class Calculator:
    """Calculator service class."""

    def __init__(self, initial: int = 0) -> None:
        """Initialize calculator."""
        self.value = initial

    def add(self, amount: int) -> int:
        """Add amount to total."""
        self.value += amount
        return self.value

    @classmethod
    def create_default(cls) -> "Calculator":
        """Factory class method."""
        return cls(0)

    @staticmethod
    def is_positive(val: int) -> bool:
        """Check if number is positive."""
        return val > 0

    class SubEngine:
        """Nested engine class."""

        def compute(self, factor: int) -> int:
            """Nested method."""
            return factor * 2
