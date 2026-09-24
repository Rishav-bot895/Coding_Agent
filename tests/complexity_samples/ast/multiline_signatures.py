"""Fixture with multiline signatures."""

from typing import Any


def complex_multiline_func(
    first_arg: int,
    second_arg: str = "default_value",
    *var_args: float,
    keyword_only_one: bool = True,
    keyword_only_two: dict[str, int] | None = None,
    **remaining_kwargs: Any,
) -> tuple[
    int,
    str,
    float,
]:
    """Function with signature across multiple lines."""
    return (first_arg, second_arg, 1.0)


class DataProcessor:
    """Class with multiline method signatures."""

    def process_records(
        self,
        records: list[dict[str, Any]],
        filter_mode: str = "strict",
        batch_size: int = 100,
    ) -> list[dict[str, Any]]:
        """Method with multiline parameters."""
        return records[:batch_size]
