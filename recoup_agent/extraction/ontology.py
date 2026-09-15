from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RightType:
    term_type: str
    family: str
    required_fields: tuple[str, ...]
    value_kind: str
    signal_phrases: tuple[str, ...]
    description: str


_COMMITMENT = ("minimum commitment", "committed", "shall pay no less than", "minimum monthly fee", "platform fee")
_USAGE = ("included", "in excess of", "overage", "per unit", "additional units", "tier")
_PRICING = ("increase", "escalat", "CPI", "anniversary", "discount", "promotional", "credit of", "expires")
_TERM = ("Initial Term", "renew", "automatically", "notice", "terminate", "Effective Date")

FINANCIAL_RIGHT_TYPES: dict[str, RightType] = {
    "committed_minimum": RightType("committed_minimum", "commitment", ("value",), "amount", _COMMITMENT, "Committed minimum fee"),
    "committed_seats": RightType("committed_seats", "commitment", ("value",), "quantity", _COMMITMENT, "Committed seat count"),
    "seat_price": RightType("seat_price", "commitment", ("value",), "amount", _COMMITMENT, "Price per committed seat"),
    "included_units": RightType("included_units", "usage", ("value",), "quantity", _USAGE, "Included usage quantity"),
    "overage_rate": RightType("overage_rate", "usage", ("value",), "rate", _USAGE, "Usage overage rate"),
    "overage_tier": RightType("overage_tier", "usage", ("value", "tier_up_to"), "rate", _USAGE, "Tiered overage rate"),
    "escalator": RightType("escalator", "pricing_changes", ("value", "effective_date"), "percentage", _PRICING, "Price escalator"),
    "discount": RightType("discount", "pricing_changes", ("value",), "amount_or_percentage", _PRICING, "Discount or promotion"),
    "term_start": RightType("term_start", "term", ("effective_date",), "date", _TERM, "Initial term start"),
    "term_end": RightType("term_end", "term", ("effective_date",), "date", _TERM, "Initial term end"),
    "auto_renewal": RightType("auto_renewal", "term", ("value",), "months", _TERM, "Automatic renewal period"),
    "renewal_notice_days": RightType("renewal_notice_days", "term", ("value",), "days", _TERM, "Renewal notice period"),
}


def families() -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for term_type, right in FINANCIAL_RIGHT_TYPES.items():
        result.setdefault(right.family, []).append(term_type)
    return result
