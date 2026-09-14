# ruff: noqa
"""Bug sample 09: Referencing misspelled variable name causing NameError."""


def compute_tax(subtotal: float, rate: float) -> float:
    """Calculate tax based on subtotal and rate."""
    tax_amount = subtotal * rate
    # Bug: tax_amont is misspelled, triggering NameError
    return round(tax_amont, 2)


if __name__ == "__main__":
    compute_tax(100.0, 0.08)
