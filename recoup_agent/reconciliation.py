"""Deterministic reconciliation engine.

This is the trustworthy heart of Recoup: every dollar figure in the demo is
computed here in plain Python, never guessed by an LLM. The agents narrate and
ground these findings, but the numbers come from this module.

Four leakage rules:
  1. unenforced_minimum  - billed below the committed monthly minimum
  2. unbilled_overage    - usage above the included tier was not charged
  3. expired_discount    - a discount past its expiry was still applied
  4. missed_escalator    - an annual price escalator was not applied
"""
from __future__ import annotations
from datetime import date


def _parse(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def reconcile(contract: dict, usage: dict, invoice: dict, period: str) -> list[dict]:
    """Compare one customer's contract entitlements against what was billed."""
    findings: list[dict] = []
    cid, cname = contract["customer_id"], contract["customer_name"]
    seq = 1

    def add(ftype: str, title: str, amount: float, clause_ref: str, detail: str) -> None:
        nonlocal seq
        findings.append({
            "finding_id": f"F-{cid.upper()}-{seq:03d}",
            "customer_id": cid, "customer_name": cname,
            "type": ftype, "title": title,
            "monthly_recoverable": round(float(amount), 2),
            "clause_ref": clause_ref, "detail": detail,
            "status": "open",
        })
        seq += 1

    period_d = _parse(period + "-01")

    # Rule 1 - committed minimum not enforced
    minimum = contract.get("committed_minimum_monthly", 0)
    base = invoice.get("base_charge", 0)
    if minimum and base + 1e-9 < minimum:
        add("unenforced_minimum", "Committed monthly minimum not enforced",
            minimum - base, "committed_minimum",
            f"Contract commits to a ${minimum:,.0f}/mo minimum; only ${base:,.0f} was billed.")

    # Rule 2 - usage overage not billed
    included = contract.get("included_units", 0)
    rate = contract.get("overage_rate", 0)
    used = usage.get("units", 0)
    overage_units = max(0, used - included)
    expected_overage = overage_units * rate
    billed_overage = invoice.get("overage_charge", 0)
    if expected_overage - billed_overage > 0.01:
        add("unbilled_overage", "Usage overage not billed",
            expected_overage - billed_overage, "overage",
            f"{used:,} units used vs {included:,} included; {overage_units:,} overage units "
            f"at ${rate:,.2f} = ${expected_overage:,.0f}, but ${billed_overage:,.0f} was billed.")

    # Rule 3 - expired discount still applied
    by_name = {d["name"]: d for d in contract.get("discounts", [])}
    for applied in invoice.get("discounts_applied", []):
        d = by_name.get(applied["name"])
        exp = _parse(d.get("expires")) if d else None
        if exp and period_d and period_d > exp:
            amount = applied.get("amount", 0)
            add("expired_discount", "Expired discount still applied",
                amount, "discount",
                f"'{applied['name']}' expired {d['expires']} but ${amount:,.0f} was still "
                f"deducted in {period}.")

    # Rule 4 - annual escalator not applied
    esc = contract.get("annual_escalator_pct", 0)
    esc_date = _parse(contract.get("escalator_effective_date"))
    if esc and esc_date and period_d and period_d >= esc_date and abs(base - minimum) < 0.01:
        expected_base = minimum * (1 + esc)
        if expected_base - base > 0.01:
            add("missed_escalator", "Annual price escalator not applied",
                expected_base - base, "escalator",
                f"{esc*100:.0f}% escalator effective {contract['escalator_effective_date']} "
                f"not applied; base should be ${expected_base:,.0f} vs ${base:,.0f} billed.")

    return findings
