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
always surface as a needs_review note (never netted into findings).
"""
from __future__ import annotations
from datetime import date, timedelta

from .book_loader import match_discount
from .line_roles import SEAT_RE
from .money import is_supported, normalize_currency, quantize

CONFIDENCE_THRESHOLD = 0.85

# finding_id type codes: F-{CID}-{period}-{CODE}[-{n}] is stable across
# re-evaluations and unique across periods (Firestore upserts depend on it).
TYPECODE = {
    "unenforced_minimum": "MIN",
    "unbilled_overage": "OVR",
    "expired_discount": "DISC",
    "missed_escalator": "ESC",
    "missing_base_charge": "BASE",
    "underbilled_seats": "SEATS",
}


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
    if contract.get("confirmed") is True:
        return 1.0
    return float(contract.get("term_meta", {}).get(field, {}).get("confidence", 1.0))


def _needs_review(needs_review: list[dict] | None, contract: dict, term: str, reason: str,
                  extra: dict | None = None, suggested_action: str | None = None) -> None:
    if needs_review is None:
        return
    entry = {
        "customer_id": contract["customer_id"],
        "customer_name": contract["customer_name"],
        "term": term,
        "reason": reason,
        "suggested_action": (suggested_action or
                             f"Confirm the {term} term in the Agreements panel (or upload "
                             "a clearer copy of the agreement) and Recoup will re-evaluate."),
    }
    if extra:
        entry.update(extra)
    needs_review.append(entry)


def reconcile(contract: dict, usage: dict, invoice: dict, period: str, needs_review: list[dict] | None = None) -> list[dict]:
    """Compare one customer's contract entitlements against what was billed."""
    findings: list[dict] = []
    cid, cname = contract["customer_id"], contract["customer_name"]
    type_counts: dict[str, int] = {}

    def add(ftype: str, title: str, amount: float, clause_ref: str, detail: str,
            math: str = "", clause_text: str = "", assumption: str | None = None,
            confidence: float = 1.0, term: str = "", extra: dict | None = None,
            expected_value: float | None = None,
            actual_value: float | None = None) -> None:
        if not (clause_text or "").strip():
            _needs_review(
                needs_review, contract, term or clause_ref,
                f"{title}: computed ${amount:,.2f}/mo but no contract clause quote could be cited to ground it",
                extra={"amount": quantize(amount)},
            )
            return
        n = type_counts.get(ftype, 0) + 1
        type_counts[ftype] = n
        finding = {
            "finding_id": f"F-{cid.upper()}-{period.replace('-', '')}-{TYPECODE[ftype]}"
                          + (f"-{n}" if n > 1 else ""),
            "customer_id": cid, "customer_name": cname,
            "type": ftype, "title": title,
            "monthly_recoverable": quantize(amount),
            "currency": "USD",
            "clause_ref": clause_ref, "detail": detail,
            "math": math, "clause_text": clause_text, "provenance": clause_text,
            "period": period, "confidence_score": round(confidence, 4),
            "status": "open",
        }
        if assumption:
            finding["assumption"] = assumption
        if expected_value is not None:
            finding["expected_value"] = quantize(expected_value)
        if actual_value is not None:
            finding["actual_value"] = quantize(actual_value)
        if extra:
            finding.update(extra)
        findings.append(finding)

    period_d = _parse(period + "-01")
    period_end = (date(period_d.year + (period_d.month == 12),
                       1 if period_d.month == 12 else period_d.month + 1, 1)
                  if period_d else None)
    period_end = (period_end.replace(day=1) - timedelta(days=1)
                  if period_end else None)

    def _surface_credits() -> None:
        credits = invoice.get("credits_applied") or []
        if not credits:
            return
        total = sum(float(c.get("amount", 0)) for c in credits)
        descriptions = "; ".join(c.get("description", "") for c in credits)
        _needs_review(
            needs_review, contract, "credits_applied",
            f"${total:,.2f} in credits/refunds this period ({descriptions}); "
            "confirm whether they offset a finding or are an unlinked credit/refund",
            extra={"amount": quantize(total)},
        )

    inv_ccy = normalize_currency(invoice.get("currency"))
    con_ccy = normalize_currency(contract.get("currency"))
    if (invoice.get("currency_mixed")
            or (invoice.get("currency") and not inv_ccy)
            or (contract.get("currency") and not con_ccy)
            or (inv_ccy and not is_supported(inv_ccy))
            or (con_ccy and not is_supported(con_ccy))
            or (inv_ccy and con_ccy and inv_ccy != con_ccy)):
        _needs_review(needs_review, contract, "currency",
                      "invoice and contract currencies are missing, unsupported, mixed, or disagree")
        _surface_credits()
        return findings
    eff = _parse(contract.get("effective_date") or contract.get("term_start"))
    term_end = _parse(contract.get("term_end"))
    if eff and period_end and period_end < eff:
        _needs_review(needs_review, contract, "effective_date",
                      f"period {period} precedes contract effective date {eff}")
        _surface_credits()
        return findings
    if term_end and period_d and period_d > term_end and not contract.get("auto_renew_months"):
        _needs_review(needs_review, contract, "term_end",
                      f"contract term ended {contract['term_end']}; invoices continue with no "
                      "auto-renewal clause — confirm renewal terms/rates")
        _surface_credits()
        return findings

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

    # Rule 1 - committed minimum not enforced. Conflicting or undated minimum
    # terms fail closed to review regardless of confidence or confirmation.
    if "term_conflicts" not in contract and "unresolved_terms" not in contract:
        from .normalizer import detect_term_conflicts
        detect_term_conflicts(contract)
    min_conflicts = [c for c in contract.get("term_conflicts") or []
                     if c.get("term") == "committed_minimum"]
    min_unresolved = [u for u in contract.get("unresolved_terms") or []
                      if u.get("term") == "committed_minimum"]

    def _loc(cand: dict) -> str:
        parts = []
        if cand.get("section_ref"):
            ref = str(cand["section_ref"])
            parts.append(ref if not ref[0].isdigit() else f"\u00a7{ref}")
        if cand.get("page") is not None:
            parts.append(f"p.{cand['page']}")
        return " ".join(parts) or "extracted term"

    # Non-minimum scalar conflicts: fail closed to review, one entry each;
    # the corresponding rules skip because the field is None.
    SCALAR_CONFLICT_LABELS = {
        "committed_seats": "committed seats", "seat_price": "seat price",
        "included_units": "included units", "overage_rate": "overage rate",
        "escalator": "escalator", "term_start": "term start",
        "term_end": "term end", "auto_renewal": "auto-renewal",
        "renewal_notice_days": "renewal notice",
    }
    other_conflicts = [c for c in contract.get("term_conflicts") or []
                       if c.get("term") != "committed_minimum"]
    conflicted_terms = {c.get("term") for c in other_conflicts}
    for conflict in other_conflicts:
        label = SCALAR_CONFLICT_LABELS.get(conflict.get("term"),
                                         str(conflict.get("term")).replace("_", " "))
        cands = " vs ".join(
            f"{cand.get('value')} ({_loc(cand)})"
            for cand in conflict.get("candidates") or [])
        _needs_review(
            needs_review, contract, conflict.get("term"),
            f"Conflicting values for {label}: {cands} \u2014 choose which governs",
            extra={"term_conflicts": [conflict]})

    minimum, minimum_provenance = minimum_for_period(contract, period)
    base = invoice.get("base_charge")
    minimum_conf = _confidence(contract, "committed_minimum_monthly")
    # Rule 6 precompute: a missing base line with other charges present is
    # handled by Rule 6 instead of Rule 1 (avoids double-counting).
    base_missing = base is not None and base <= 0.005
    has_other_lines = (invoice.get("overage_charge") or 0) > 0.005 or bool(
        invoice.get("discounts_applied") or invoice.get("credits_applied"))
    if prorated:
        pass
    elif min_conflicts or min_unresolved:
        reasons = []
        for conflict in min_conflicts:
            cands = " vs ".join(
                f"${cand.get('amount'):,.2f} ({_loc(cand)})"
                for cand in conflict.get("candidates") or [])
            reasons.append(
                f"Conflicting minimums: {cands} for periods from "
                f"{conflict.get('effective_date')} \u2014 choose which governs")
        for item in min_unresolved:
            reasons.append(
                f"Undated amendment sets minimum ${item.get('amount'):,.2f} "
                f"({_loc(item)}) \u2014 give it an effective month or dismiss it")
        _needs_review(
            needs_review, contract, "committed_minimum", "; ".join(reasons),
            extra={"term_conflicts": min_conflicts,
                   "unresolved_terms": min_unresolved},
        )
    elif minimum is None or base is None or minimum_conf < CONFIDENCE_THRESHOLD:
        _needs_review(
            needs_review,
            contract,
            "committed_minimum_monthly",
            f"missing or low-confidence committed minimum (confidence={minimum_conf:.2f})",
        )
    elif minimum and base + 1e-9 < minimum and not (base_missing and has_other_lines):
        amount = minimum - base
        add("unenforced_minimum", "Committed monthly minimum not enforced",
            amount, "committed_minimum",
            f"Contract commits to a ${minimum:,.0f}/mo minimum; only ${base:,.0f} was billed.",
            math=f"committed minimum ${minimum:,.0f}/mo − billed ${base:,.0f}/mo = ${amount:,.0f}/mo",
            clause_text=(minimum_provenance
                         or _clause_text(contract, "committed_minimum", "committed_minimum_monthly")),
            confidence=minimum_conf, term="committed_minimum_monthly",
            expected_value=minimum, actual_value=base)

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
    if included is not None and included < 0:
        _needs_review(needs_review, contract, "included_units", "negative usage quantity")
    elif used is not None and used < 0:
        _needs_review(needs_review, contract, "included_units/overage", "negative usage quantity")
    elif included is None or (rate is None and not tiers) or used is None or billed_overage is None or min(included_conf, rate_conf) < CONFIDENCE_THRESHOLD:
        if not (conflicted_terms & {"included_units", "overage_rate"}):
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
            if billed_overage > 0.005:
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
                    extra={"overage_tiers": [{"up_to": t.get("up_to"), "rate": t["rate"]} for t in tiers]},
                    expected_value=expected_overage, actual_value=billed_overage)
            else:
                add("unbilled_overage", "Usage overage not billed",
                    amount, "overage",
                    f"{used:,} units used vs {included:,} included; {overage_units:,} overage units "
                    f"at {_fmt_rate(rate)} = ${expected_overage:,.0f}, but ${billed_overage:,.0f} was billed.",
                    math=math,
                    clause_text=_clause_text(contract, "overage", "overage_rate"),
                    confidence=min(included_conf, rate_conf), term="included_units/overage_rate",
                    expected_value=expected_overage, actual_value=billed_overage)

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
                    confidence=discount_conf, term="discounts",
                    expected_value=(base + amount) if base is not None else None,
                    actual_value=base)

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
        elif period_end and period_end >= esc_date:
            # Effective date counts as step 1 (same as day-01 semantics);
            # step count is evaluated against period_end.
            steps = (1 + (period_end.year - esc_date.year)
                     - (1 if (period_end.month, period_end.day)
                        < (esc_date.month, esc_date.day) else 0))
            try:
                ann = esc_date.replace(year=esc_date.year + steps - 1)
            except ValueError:  # Feb 29 anniversaries resolve to Feb 28
                ann = esc_date.replace(year=esc_date.year + steps - 1, day=28)
            # An anniversary falling inside the period counts as a full step:
            # the invoice for that period is issued on/after the anniversary
            # and therefore lies in the new contract year.
            mid_period = ann.day != 1 and period_d < ann <= period_end
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
                noun = "anniversary" if steps == 1 else "anniversaries"
                add("missed_escalator", "Annual price escalator not applied",
                    amount, "escalator",
                    f"{esc*100:.0f}% escalator, {steps} {noun} since "
                    f"{contract['escalator_effective_date']}; base should be "
                    f"${expected_base:,.2f} vs ${baseline:,.2f} billed.",
                    math=math,
                    clause_text=_clause_text(contract, "escalator", "annual_escalator_pct"),
                    confidence=min(esc_conf, esc_date_conf), term="annual_escalator_pct",
                    assumption=("Escalator compounds annually on each anniversary of the effective date"
                                + (f"; anniversary {ann} falls within the billing period and the "
                                   "full escalated rate applies to that period." if mid_period else ".")),
                    extra={"escalator_steps": steps, "anniversary_mid_period": mid_period},
                    expected_value=expected_base, actual_value=baseline)

    # Rule 5 - post-term billing (review only, no dollars)
    term_end = _parse(contract.get("term_end"))
    if term_end and period_d and period_d > term_end:
        if contract.get("auto_renew_months") and esc and esc_date_raw is None:
            _needs_review(
                needs_review, contract, "term_end",
                f"contract term ended {contract['term_end']} and auto-renews; escalator has no "
                "effective date — check renewal pricing",
            )

    # Rule 6 - missing base line item (invoice carries non-base charges but no
    # base line against a committed minimum; replaces Rule 1 for this period to
    # avoid double-counting the same shortfall)
    if (not prorated and minimum and minimum_conf >= CONFIDENCE_THRESHOLD
            and base is not None and base_missing and has_other_lines):
        add("missing_base_charge", "Committed minimum base charge missing from invoice",
            minimum, "committed_minimum",
            f"Contract commits to a ${minimum:,.2f}/mo minimum; the invoice has other "
            "line items but no base charge.",
            math=f"committed minimum ${minimum:,.2f}/mo − billed base $0.00 = ${minimum:,.2f}",
            clause_text=(minimum_provenance
                         or _clause_text(contract, "committed_minimum", "committed_minimum_monthly")),
            confidence=minimum_conf, term="committed_minimum_monthly",
            expected_value=minimum, actual_value=0.0)

    # Rule 7 - seats underbilled
    committed_seats = contract.get("committed_seats")
    seat_price = contract.get("seat_price")
    if committed_seats is not None and committed_seats < 0:
        _needs_review(needs_review, contract, "committed_seats", "negative seat quantity")
        committed_seats = None
    if committed_seats and seat_price:
        billed_seats = invoice.get("seat_units")
        usage_metric = (usage.get("metric") or "").lower()
        usage_metrics = usage.get("_metrics") or {}
        seat_metric = SEAT_RE.search(usage_metric) or any(
            SEAT_RE.search(m or "") for m in usage_metrics)
        actual_seats = usage.get("units") if seat_metric else None
        if billed_seats is None:
            _needs_review(
                needs_review, contract, "committed_seats",
                f"contract commits {committed_seats:g} seats at ${seat_price:,.2f} but no seat "
                "line found on invoice",
            )
        else:
            if billed_seats < 0:
                _needs_review(needs_review, contract, "seat_units", "negative seat quantity")
                billed_seats = None
            if billed_seats is None:
                expected_seats = 0
            else:
                expected_seats = max(committed_seats, actual_seats or 0)
            if billed_seats is not None and expected_seats - billed_seats >= 1:
                short = expected_seats - billed_seats
                amount = short * seat_price
                detail_bits = f"committed {committed_seats:g}" + (
                    f", active {actual_seats:g}" if actual_seats is not None else "")
                add("underbilled_seats", "Committed seats not fully billed",
                    amount, "seats",
                    f"Contract commits {committed_seats:g} seats at ${seat_price:,.2f}; "
                    f"only {billed_seats:g} were billed.",
                    math=(f"expected {expected_seats:g} seats ({detail_bits}) − billed "
                          f"{billed_seats:g} = {short:g} × ${seat_price:,.2f} = ${amount:,.2f}"),
                    clause_text=_clause_text(contract, "seats", "committed_seats"),
                    confidence=min(_confidence(contract, "committed_seats"),
                                   _confidence(contract, "seat_price")),
                    term="committed_seats/seat_price",
                    expected_value=expected_seats * seat_price,
                    actual_value=billed_seats * seat_price)

    # Credits/refunds never offset base or discounts_applied; if this customer
    # produced findings this period, flag the credits for a human to confirm
    # they don't already cover them.
    _surface_credits()

    return findings
