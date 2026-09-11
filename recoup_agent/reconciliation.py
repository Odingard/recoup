"""Deterministic reconciliation engine.

This is the trustworthy heart of Recoup: every dollar figure in the demo is
computed here in plain Python, never guessed by an LLM. The agents narrate and
ground these findings, but the numbers come from this module.

Four leakage rules:
  1. unenforced_minimum  - billed below the committed monthly minimum
  2. unbilled_overage    - usage above the included tier was not charged
     (flat overage_rate or tiered overage_tiers bands)
  3. expired_discount    - a discount past its expiry was still applied
  4. missed_escalator    - an annual price escalator was not applied
     (compounds once per anniversary of the effective date)

Line hygiene: prorated invoices skip rules 1 and 4 entirely; tax lines are
excluded from base; credits/refunds are kept out of discounts_applied and
surface as a needs_review note when other findings exist.
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
            confidence: float = 1.0, term: str = "", extra: dict | None = None) -> None:
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
        if extra:
            finding.update(extra)
        findings.append(finding)
        seq += 1

    period_d = _parse(period + "-01")
    findings_before = len(findings)

    # Prorated/partial-period invoices: minimum and escalator checks are
    # meaningless for a partial month — record a review note and skip them.
    prorated = bool(invoice.get("prorated"))
    if prorated:
        _needs_review(
            needs_review,
            contract,
            "base_charge",
            f"invoice contains prorated line(s) (net ${invoice.get('proration_amount', 0):,.2f}); "
            "partial-period billing — committed-minimum and escalator checks skipped",
        )

    # Rule 1 - committed minimum not enforced
    minimum, minimum_provenance = minimum_for_period(contract, period)
    base = invoice.get("base_charge")
    minimum_conf = _confidence(contract, "committed_minimum_monthly")
    if prorated:
        pass
    elif minimum is None or base is None or minimum_conf < CONFIDENCE_THRESHOLD:
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
    tiers = contract.get("overage_tiers")
    used = usage.get("units")
    billed_overage = invoice.get("overage_charge")
    included_conf = _confidence(contract, "included_units")
    if tiers:
        rate_conf = _confidence(contract, "overage_tiers")
        rate_term = "overage_tiers"
    else:
        rate_conf = _confidence(contract, "overage_rate")
        rate_term = "overage_rate"
    if included is None or (rate is None and not tiers) or used is None or billed_overage is None or min(included_conf, rate_conf) < CONFIDENCE_THRESHOLD:
        _needs_review(
            needs_review,
            contract,
            f"included_units/{rate_term}",
            f"missing or low-confidence included units/overage rate (confidence={min(included_conf, rate_conf):.2f})",
        )
    else:
        overage_units = max(0, used - included)
        if tiers:
            expected_overage = 0.0
            bands: list[str] = []
            lower = 0.0
            remaining = overage_units
            for tier in tiers:
                up_to = tier.get("up_to")
                band_units = remaining if up_to is None else min(remaining, max(0.0, up_to - lower))
                if band_units > 0:
                    band_amount = band_units * tier["rate"]
                    expected_overage += band_amount
                    hi = f"{lower + band_units:,.0f}"
                    bands.append(f"{lower:,.0f}–{hi} × {_fmt_rate(tier['rate'])} = ${band_amount:,.2f}")
                    lower += band_units
                    remaining -= band_units
                elif up_to is not None:
                    lower = up_to
                if remaining <= 0:
                    break
            math = (f"{overage_units:,} overage units: " + "; ".join(bands)
                    + f"; total ${expected_overage:,.2f}/mo")
        else:
            expected_overage = overage_units * rate
            math = (f"{used:,} units − {included:,} included = {overage_units:,} units "
                    f"× {_fmt_rate(rate)} = ${expected_overage:,.2f}/mo")
        if expected_overage - billed_overage > 0.01:
            amount = expected_overage - billed_overage
            math += f" − ${billed_overage:,.2f} already billed = ${amount:,.2f}/mo"
            if tiers:
                tier_provenance = next((t.get("provenance") for t in tiers if t.get("provenance")), "")
                add("unbilled_overage", "Usage overage not billed",
                    amount, "overage",
                    f"{used:,} units used vs {included:,} included; {overage_units:,} overage units "
                    f"across {len(tiers)} tier(s) = ${expected_overage:,.2f}, but ${billed_overage:,.0f} was billed.",
                    math=math,
                    clause_text=tier_provenance or _clause_text(contract, "overage", "overage_tiers"),
                    confidence=min(included_conf, rate_conf), term="included_units/overage_tiers",
                    extra={"overage_tiers": [{"up_to": t.get("up_to"), "rate": t["rate"]} for t in tiers]})
            else:
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
        elif prorated:
            pass
        elif period_d and period_d >= esc_date:
            steps = (1 + (period_d.year - esc_date.year)
                     - (1 if (period_d.month, period_d.day) < (esc_date.month, esc_date.day) else 0))
            expected_base = minimum * (1 + esc) ** steps
            baseline = max(base, minimum)
            amount = expected_base - baseline
            if amount > 0.01:
                if steps == 1:
                    math = (f"${minimum:,.0f}/mo × (1 + {esc:.0%}) = ${expected_base:,.0f}/mo "
                            f"− billed ${baseline:,.0f}/mo = ${amount:,.0f}/mo")
                else:
                    math = (f"${minimum:,.0f}/mo × (1 + {esc:.0%})^{steps} = ${expected_base:,.2f}/mo "
                            f"− billed ${baseline:,.2f}/mo = ${amount:,.2f}/mo")
                ann = "anniversary" if steps == 1 else "anniversaries"
                add("missed_escalator", "Annual price escalator not applied",
                    amount, "escalator",
                    f"{esc*100:.0f}% escalator, {steps} {ann} since "
                    f"{contract['escalator_effective_date']}; base should be "
                    f"${expected_base:,.2f} vs ${baseline:,.2f} billed.",
                    math=math,
                    clause_text=_clause_text(contract, "escalator", "annual_escalator_pct"),
                    confidence=min(esc_conf, esc_date_conf), term="annual_escalator_pct",
                    assumption="Escalator compounds annually on each anniversary of the effective date.",
                    extra={"escalator_steps": steps})

    # Credits/refunds never offset base or discounts_applied; if this customer
    # produced findings this period, flag the credits for a human to confirm
    # they don't already cover them.
    credits = invoice.get("credits_applied") or []
    if credits and len(findings) > findings_before:
        total = sum(float(c.get("amount", 0)) for c in credits)
        descriptions = "; ".join(c.get("description", "") for c in credits)
        _needs_review(
            needs_review,
            contract,
            "credits_applied",
            f"${total:,.2f} in credits/refunds this period ({descriptions}); "
            "confirm they do not already offset the findings above",
            extra={"amount": round(total, 2)},
        )

    return findings
