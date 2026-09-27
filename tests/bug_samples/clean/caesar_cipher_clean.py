"""Clean sample: Caesar cipher encryption and decryption."""


def caesar_cipher(text: str, shift: int) -> str:
    """Encrypt or decrypt text using Caesar cipher shift."""
    result = []
    for ch in text:
        if "a" <= ch <= "z":
            shifted = chr((ord(ch) - ord("a") + shift) % 26 + ord("a"))
            result.append(shifted)
        elif "A" <= ch <= "Z":
            shifted = chr((ord(ch) - ord("A") + shift) % 26 + ord("A"))
            result.append(shifted)
        else:
            result.append(ch)
    return "".join(result)


def main() -> None:
    original = "Hello, World!"
    encrypted = caesar_cipher(original, 3)
    assert encrypted == "Khoor, Zruog!"
    decrypted = caesar_cipher(encrypted, -3)
    assert decrypted == original


if __name__ == "__main__":
    main()

