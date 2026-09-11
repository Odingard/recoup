"""Shared invoice line-item classifier.

Maps a billing line to a role so that proration, tax, credits/refunds, and
unmapped negative lines never contaminate base_charge, overage_charge, or
discounts_applied.
"""
from __future__ import annotations

import re

PRORATION_RE = re.compile(r"\b(prorat\w*|partial period|unused time|remaining time)\b", re.I)
TAX_RE = re.compile(r"\b(sales tax|vat|gst|hst|pst|qst|tax)\b", re.I)
CREDIT_RE = re.compile(r"\b(credit note|credit memo|service credit|credit|refund|chargeback|goodwill|adjustment|write-?off)\b", re.I)


def classify_line(description: str, amount: float, *, usage_type: str | None = None,
                  proration: bool = False, contract_discounts: list[dict] | None = None) -> str:
    """-> 'proration' | 'tax' | 'discount' | 'credit' | 'overage' | 'base'"""
    from .book_loader import _match_discount_strict

    desc = description or ""
    if proration or PRORATION_RE.search(desc):
        return "proration"
    if TAX_RE.search(desc):
        return "tax"
    if amount < 0:
        if _match_discount_strict(contract_discounts or [], desc):
            return "discount"
        if CREDIT_RE.search(desc):
            return "credit"
        return "discount"
    if usage_type == "metered" or "overage" in desc.lower():
        return "overage"
    return "base"
