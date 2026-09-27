"""Clean sample: Object-oriented Stack data structure."""

from typing import Generic, TypeVar

T = TypeVar("T")


class Stack(Generic[T]):
    """Generic LIFO stack."""

    def __init__(self) -> None:
        self._items: list[T] = []

    def push(self, item: T) -> None:
        self._items.append(item)

    def pop(self) -> T:
        if self.is_empty():
            raise IndexError("pop from empty stack")
        return self._items.pop()

    def peek(self) -> T:
        if self.is_empty():
            raise IndexError("peek from empty stack")
        return self._items[-1]

    def is_empty(self) -> bool:
        return len(self._items) == 0

    def size(self) -> int:
        return len(self._items)


def main() -> None:
    s: Stack[int] = Stack()
    s.push(10)
    s.push(20)
    assert s.peek() == 20
    assert s.pop() == 20
    assert s.pop() == 10
    assert s.is_empty() is True


if __name__ == "__main__":
    main()

