"""Fixture with async functions and decorators."""

from collections.abc import Callable
from typing import Any


def my_decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
    return fn


def second_decorator(param: str) -> Callable[..., Any]:
    def wrapper(fn: Callable[..., Any]) -> Callable[..., Any]:
        return fn

    return wrapper


@my_decorator
@second_decorator("config")
def decorated_function(data: list[int]) -> int:
    """Decorated function."""
    return sum(data)


async def async_fetch(url: str, timeout: float = 5.0) -> str:
    """Asynchronous worker function."""
    return f"fetched {url}"


@my_decorator
class AsyncService:
    """Class with async methods and decorators."""

    @my_decorator
    async def async_process(self, item: str) -> str:
        """Async method."""
        return item.upper()
