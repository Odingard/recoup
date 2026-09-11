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

from .book_loader import match_discount

CONFIDENCE_THRESHOLD = 0.85


def _parse(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _fmt_rate(x) -> str:
    return "$" + f"{x:,.4f}".rstrip("0").rstrip(".")


def _clause_text(contract: dict, clause_ref: str, term_field: str) -> str:
    return (
        contract.get("clauses", {}).get(clause_ref)
        or contract.get("term_meta", {}).get(term_field, {}).get("provenance")
        or ""
    )


def minimum_for_period(contract: dict, period: str) -> tuple[float | None, str | None]:
    """Resolve the committed minimum in force for a YYYY-MM period.

    Contracts may carry a minimum_schedule (amendments): each entry is
    {"amount", "effective_date"|None, "provenance"}. Take the qualifying entry
    with the latest effective_date (None counts as earliest); if none qualify,
    use the earliest-dated entry. No schedule -> committed_minimum_monthly.
    """
    schedule = contract.get("minimum_schedule")
    if not schedule:
        return contract.get("committed_minimum_monthly"), \
            contract.get("term_meta", {}).get("committed_minimum_monthly", {}).get("provenance")

    period_start = _parse(period + "-01")

    def sort_key(entry: dict):
        d = _parse(entry.get("effective_date"))
        return (d is not None, d or date.min)

    qualifying = [e for e in schedule
                  if (d := _parse(e.get("effective_date"))) is None or (period_start and d <= period_start)]
    if qualifying:
        chosen = max(qualifying, key=sort_key)
    else:
        chosen = min(schedule, key=sort_key)
    return chosen.get("amount"), chosen.get("provenance")


def _confidence(contract: dict, field: str) -> float:
    return float(contract.get("term_meta", {}).get(field, {}).get("confidence", 1.0))


def _needs_review(needs_review: list[dict] | None, contract: dict, term: str, reason: str,
                  extra: dict | None = None) -> None:
    if needs_review is None:
        return
    entry = {
        "customer_id": contract["customer_id"],
        "customer_name": contract["customer_name"],
        "term": term,
        "reason": reason,
    }
    if extra:
        entry.update(extra)
    needs_review.append(entry)


def reconcile(contract: dict, usage: dict, invoice: dict, period: str, needs_review: list[dict] | None = None) -> list[dict]:
    """Compare one customer's contract entitlements against what was billed."""
    findings: list[dict] = []
    cid, cname = contract["customer_id"], contract["customer_name"]
    seq = 1

    def add(ftype: str, title: str, amount: float, clause_ref: str, detail: str,
            math: str = "", clause_text: str = "", assumption: str | None = None,
            confidence: float = 1.0, term: str = "") -> None:
        nonlocal seq
        if not (clause_text or "").strip():
            _needs_review(
                needs_review, contract, term or clause_ref,
                f"{title}: computed ${amount:,.2f}/mo but no contract clause quote could be cited to ground it",
                extra={"amount": round(amount, 2)},
            )
            return
        finding = {
            "finding_id": f"F-{cid.upper()}-{seq:03d}",
            "customer_id": cid, "customer_name": cname,
            "type": ftype, "title": title,
            "monthly_recoverable": round(float(amount), 2),
            "clause_ref": clause_ref, "detail": detail,
            "math": math, "clause_text": clause_text, "provenance": clause_text,
            "period": period, "confidence_score": round(confidence, 4),
            "status": "open",
        }
        if assumption:
            finding["assumption"] = assumption
        findings.append(finding)
        seq += 1

    period_d = _parse(period + "-01")

    # Rule 1 - committed minimum not enforced
    minimum, minimum_provenance = minimum_for_period(contract, period)
    base = invoice.get("base_charge")
    minimum_conf = _confidence(contract, "committed_minimum_monthly")
    if minimum is None or base is None or minimum_conf < CONFIDENCE_THRESHOLD:
        _needs_review(
            needs_review,
            contract,
            "committed_minimum_monthly",
            f"missing or low-confidence committed minimum (confidence={minimum_conf:.2f})",
        )
    elif minimum and base + 1e-9 < minimum:
        amount = minimum - base
        add("unenforced_minimum", "Committed monthly minimum not enforced",
            amount, "committed_minimum",
            f"Contract commits to a ${minimum:,.0f}/mo minimum; only ${base:,.0f} was billed.",
            math=f"committed minimum ${minimum:,.0f}/mo − billed ${base:,.0f}/mo = ${amount:,.0f}/mo",
            clause_text=(minimum_provenance
                         or _clause_text(contract, "committed_minimum", "committed_minimum_monthly")),
            confidence=minimum_conf, term="committed_minimum_monthly")

    # Rule 2 - usage overage not billed
    included = contract.get("included_units")
    rate = contract.get("overage_rate")
    used = usage.get("units")
    billed_overage = invoice.get("overage_charge")
    included_conf = _confidence(contract, "included_units")
    rate_conf = _confidence(contract, "overage_rate")
    if included is None or rate is None or used is None or billed_overage is None or min(included_conf, rate_conf) < CONFIDENCE_THRESHOLD:
        _needs_review(
            needs_review,
            contract,
            "included_units/overage_rate",
            f"missing or low-confidence included units/overage rate (confidence={min(included_conf, rate_conf):.2f})",
        )
    else:
        overage_units = max(0, used - included)
        expected_overage = overage_units * rate
        if expected_overage - billed_overage > 0.01:
            amount = expected_overage - billed_overage
            math = (f"{used:,} units − {included:,} included = {overage_units:,} units "
                    f"× {_fmt_rate(rate)} = ${expected_overage:,.2f}/mo")
            if billed_overage > 0.005:
                math += f" − ${billed_overage:,.2f} already billed = ${amount:,.2f}/mo"
            add("unbilled_overage", "Usage overage not billed",
                amount, "overage",
                f"{used:,} units used vs {included:,} included; {overage_units:,} overage units "
                f"at {_fmt_rate(rate)} = ${expected_overage:,.0f}, but ${billed_overage:,.0f} was billed.",
                math=math,
                clause_text=_clause_text(contract, "overage", "overage_rate"),
                confidence=min(included_conf, rate_conf), term="included_units/overage_rate")

    # Rule 3 - expired discount still applied
    discounts = contract.get("discounts")
    by_name = {d["name"]: d for d in discounts or []}
    discount_conf = _confidence(contract, "discounts")
    applied_discounts = invoice.get("discounts_applied")
    if discounts is None or applied_discounts is None or discount_conf < CONFIDENCE_THRESHOLD:
        _needs_review(
            needs_review,
            contract,
            "discounts",
            f"missing or low-confidence discounts (confidence={discount_conf:.2f})",
        )
    else:
        for applied in applied_discounts:
            d = by_name.get(match_discount(discounts, applied["name"]))
            exp = _parse(d.get("expires")) if d else None
            if exp and period_d and period_d > exp:
                amount = applied.get("amount", 0)
                pct = f"{d['value'] * 100:.0f}" if d.get("type") == "percent" else str(d.get("value"))
                add("expired_discount", "Expired discount still applied",
                    amount, "discount",
                    f"'{applied['name']}' expired {d['expires']} but ${amount:,.0f} was still "
                    f"deducted in {period}.",
                    math=(f"'{applied['name']}' discount of {pct}% is past its expiry but "
                         f"${amount:,.0f}/mo is still deducted = ${amount:,.0f}/mo"),
                    clause_text=d.get("provenance") or _clause_text(contract, "discount", "discounts"),
                    confidence=discount_conf, term="discounts")

    # Rule 4 - annual escalator not applied
    esc = contract.get("annual_escalator_pct")
    esc_date_raw = contract.get("escalator_effective_date")
    esc_date = _parse(esc_date_raw)
    esc_conf = _confidence(contract, "annual_escalator_pct")
    esc_date_conf = _confidence(contract, "escalator_effective_date")
    if esc:
        if esc_date_raw is None or min(esc_conf, esc_date_conf) < CONFIDENCE_THRESHOLD:
            _needs_review(
                needs_review,
                contract,
                "annual_escalator_pct/escalator_effective_date",
                f"missing or low-confidence escalator terms (confidence={min(esc_conf, esc_date_conf):.2f})",
            )
        elif base is None or minimum is None:
            _needs_review(
                needs_review,
                contract,
                "annual_escalator_pct",
                "escalator rule requires both base charge and committed minimum to be present",
            )
        elif period_d and period_d >= esc_date and abs(base - minimum) < 0.01:
            expected_base = minimum * (1 + esc)
            if expected_base - base > 0.01:
                amount = expected_base - base
                add("missed_escalator", "Annual price escalator not applied",
                    amount, "escalator",
                    f"{esc*100:.0f}% escalator effective {contract['escalator_effective_date']} "
                    f"not applied; base should be ${expected_base:,.0f} vs ${base:,.0f} billed.",
                    math=(f"${minimum:,.0f}/mo × (1 + {esc:.0%}) = ${expected_base:,.0f}/mo "
                         f"− billed ${base:,.0f}/mo = ${amount:,.0f}/mo"),
                    clause_text=_clause_text(contract, "escalator", "annual_escalator_pct"),
                    confidence=min(esc_conf, esc_date_conf), term="annual_escalator_pct",
                    assumption="Applies a single escalator step; earlier anniversaries are not compounded.")

    return findings
