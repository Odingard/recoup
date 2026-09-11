"""Renewal calendar: for each contract, when the term ends and when the
cancellation notice deadline falls. Contracts without term dates are listed
last with state 'unknown' so nothing is silent."""
from __future__ import annotations

from datetime import date, timedelta

from .reconciliation import _parse


def build_renewal_calendar(contracts: list[dict], today: date) -> list[dict]:
    rows = []
    for c in contracts:
        term_end = _parse(c.get("term_end"))
        notice_days = c.get("renewal_notice_days")
        deadline = term_end - timedelta(days=notice_days) if (term_end and notice_days) else None
        days_to_deadline = (deadline - today).days if deadline else None

        if term_end is None:
            state = "unknown"
        elif term_end < today:
            state = "expired"
        elif deadline and deadline <= today <= term_end:
            state = "notice_window_open"
        elif days_to_deadline is not None and days_to_deadline <= 90:
            state = "upcoming_90d"
        elif (term_end - today).days <= 90:
            state = "upcoming_90d"
        else:
            state = "later"

        meta = c.get("term_meta", {})
        provenance = (meta.get("term_end") or meta.get("auto_renew_months") or {}).get("provenance") \
            or (c.get("clauses") or {}).get("term") or ""
        rows.append({
            "customer_id": c["customer_id"],
            "customer_name": c.get("customer_name", c["customer_id"]),
            "term_start": c.get("term_start"),
            "term_end": c.get("term_end"),
            "auto_renew_months": c.get("auto_renew_months"),
            "renewal_notice_days": notice_days,
            "notice_deadline": deadline.isoformat() if deadline else None,
            "days_to_deadline": days_to_deadline,
            "state": state,
            "provenance": provenance,
        })

    def sort_key(r):
        return (r["state"] == "unknown",
                r["notice_deadline"] or "9999-12-31",
                r["term_end"] or "9999-12-31")

    return sorted(rows, key=sort_key)
