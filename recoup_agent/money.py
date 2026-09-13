"""Authoritative money utilities: Decimal + ROUND_HALF_UP at the currency's
precision. All monetary rounding in the codebase goes through here.
"""
from decimal import Decimal, ROUND_HALF_UP

CURRENCY_PRECISION = {"USD": 2}
SUPPORTED_CURRENCIES = frozenset(CURRENCY_PRECISION)


def quantize(amount, currency: str = "USD") -> float:
    """Round a monetary amount at the currency's precision (Decimal,
    ROUND_HALF_UP). Returns float for JSON."""
    places = CURRENCY_PRECISION.get((currency or "USD").upper(), 2)
    q = Decimal(1).scaleb(-places)
    return float(Decimal(str(amount)).quantize(q, rounding=ROUND_HALF_UP))


def to_cents(amount, currency: str = "USD") -> int:
    """Exact integer cents: quantize first, then Decimal multiply — never
    float-rounding."""
    places = CURRENCY_PRECISION.get((currency or "USD").upper(), 2)
    q = Decimal(1).scaleb(-places)
    d = Decimal(str(amount)).quantize(q, rounding=ROUND_HALF_UP)
    return int(d * (10 ** places))


def normalize_currency(code) -> str | None:
    """Upper-cased ISO 4217-style 3-letter code, or None."""
    if code is None:
        return None
    c = str(code).strip().upper()
    return c if len(c) == 3 and c.isalpha() else None


def is_supported(code) -> bool:
    c = normalize_currency(code)
    return c is not None and c in SUPPORTED_CURRENCIES
