"""Clean sample: Sieve of Eratosthenes prime generator."""


def sieve_of_eratosthenes(limit: int) -> list[int]:
    """Return all prime numbers up to limit."""
    if limit < 2:
        return []
    is_prime = [True] * (limit + 1)
    is_prime[0] = is_prime[1] = False
    p = 2
    while p * p <= limit:
        if is_prime[p]:
            for i in range(p * p, limit + 1, p):
                is_prime[i] = False
        p += 1
    return [i for i, prime in enumerate(is_prime) if prime]


def main() -> None:
    primes = sieve_of_eratosthenes(30)
    assert primes == [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]


if __name__ == "__main__":
    main()

