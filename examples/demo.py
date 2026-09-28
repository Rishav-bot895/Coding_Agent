from __future__ import annotations


def process_scores(scores: list[int]) -> int:
    total = 0
    # Bug: range(1, len(scores) + 1) raises IndexError on scores[i]
    for i in range(1, len(scores) + 1):
        total += scores[i]
    return total


def find_target_pairs(numbers: list[int], target: int = 100) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    n = len(numbers)
    for i in range(n):
        for j in range(i + 1, n):
            if numbers[i] + numbers[j] == target:
                pairs.append((numbers[i], numbers[j]))
    return pairs


def collatz_steps(n: int) -> int:
    steps = 0
    while n > 1:
        if n % 2 == 0:
            n = n // 2
        else:
            n = 3 * n + 1
        steps += 1
    return steps


def main() -> None:
    data = [10, 20, 30, 40, 50]
    total = process_scores(data)
    print(f"Total Score: {total}")


if __name__ == "__main__":
    main()
