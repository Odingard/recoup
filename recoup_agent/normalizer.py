from __future__ import annotations

from datetime import date
from decimal import Decimal
import re

from .identity import canonical_key
from .ingestion_doc import ContractEntitlements, Entitlement
from .document_quality import require_verified_document
from .money import quantize, to_cents


def _slugify(value: str) -> str:
    return canonical_key(value) or "unknown"


SCALAR_TERM_FIELDS = {  # term_type -> (contract field, coercion)
    "included_units": ("included_units", int), "overage_rate": ("overage_rate", float),
    "escalator": ("annual_escalator_pct", float), "seat_price": ("seat_price", float),
    "committed_seats": ("committed_seats", int), "auto_renewal": ("auto_renew_months", int),
    "renewal_notice_days": ("renewal_notice_days", int),
    "term_start": ("term_start", None), "term_end": ("term_end", None),  # value comes from effective_date
}


def _term_meta(ent: Entitlement) -> dict:
    meta = {
        "confidence": float(ent.confidence_score),
        "provenance": ent.provenance,
    }
    if ent.page is not None:
        meta["page"] = ent.page
    if ent.section_ref:
        meta["section_ref"] = ent.section_ref
    if ent.verification:
        meta["verification"] = ent.verification
    if ent.source_file:
        meta["source_file"] = ent.source_file
    return meta


def _minimum_amount(ent: Entitlement) -> tuple[float | None, dict]:
    periods = {
        period for period, pattern in (
            ("year", r"\bannual\b|\bper\s+(?:year|annum)\b|/\s*(?:year|yr)\b"),
            ("quarter", r"\bquarterly\b|\bper\s+quarter\b|/\s*quarter\b"),
            ("month", r"\bmonthly\b|\bper\s+month\b|/\s*(?:month|mo)\b"),
        )
        if re.search(pattern, ent.provenance, re.IGNORECASE)
    }
    period = ent.amount_period
    if len(periods) == 1 and period in (None, "unknown"):
        period = next(iter(periods))
    elif not periods and period is None:
        period = "month"
    if (period not in ("month", "quarter", "year")
            or (periods and periods != {period})
            or (ent.amount_period is not None and not periods)):
        return None, {"source_value": ent.value, "amount_period": period,
                      "normalization_error": "The commitment amount's period is missing or conflicts with its evidence."}
    months = {"month": 1, "quarter": 3, "year": 12}[period]
    cents = to_cents(ent.value)
    amount = quantize(Decimal(cents) / months / 100)
    if months == 1 and ent.amount_period is None:
        return amount, {}
    return amount, {
        "source_value": ent.value,
        "amount_period": period,
        "normalization_formula": f"{cents} cents / {months} months = {to_cents(amount)} cents/month (ROUND_HALF_UP)",
    }


def _normalize_escalator_date(normalized: dict, candidates: dict[str, list[Entitlement]]) -> None:
    start = normalized.get("term_start")
    effective = normalized.get("escalator_effective_date")
    if not start or effective != start:
        return
    matching = [ent for ent in candidates.get("escalator", []) if ent.effective_date == effective]
    if not matching or not all(re.search(
        r"\banniversary\s+of\s+(?:the\s+)?(?:effective\s+date|commencement\s+date|term\s+start)\b",
        ent.provenance, re.IGNORECASE,
    ) for ent in matching):
        normalized["term_meta"]["escalator_effective_date"] = {
            **normalized["term_meta"]["escalator_effective_date"],
            "confidence": 0.0,
            "normalization_error": "Confirm whether the first increase occurs at commencement or its anniversary.",
        }
        normalized["escalator_effective_date"] = None
        return
    try:
        anchor = date.fromisoformat(start)
    except ValueError:
        normalized["term_meta"]["escalator_effective_date"] = {
            **normalized["term_meta"]["escalator_effective_date"], "confidence": 0.0}
        normalized["escalator_effective_date"] = None
        return
    try:
        first_increase = anchor.replace(year=anchor.year + 1)
    except ValueError:
        first_increase = anchor.replace(year=anchor.year + 1, day=28)
    normalized["escalator_effective_date"] = first_increase.isoformat()
    normalized["term_meta"]["escalator_effective_date"] = {
        **normalized["term_meta"]["escalator_effective_date"],
        "source_effective_date": effective,
        "normalization_formula": f"first anniversary of {effective} = {first_increase}",
    }


def normalize_contract_entitlements(contract: ContractEntitlements) -> dict:
    require_verified_document(contract.structural_verification)
    candidates: dict[str, list[Entitlement]] = {}
    normalized = {
        "structural_verification": contract.structural_verification,
        "customer_name": contract.customer_name,
        "customer_id": _slugify(contract.customer_name),
        "committed_minimum_monthly": None,
        "minimum_schedule": [],
        "included_units": None,
        "overage_rate": None,
        "discounts": [],
        "annual_escalator_pct": None,
        "escalator_effective_date": None,
        "term_meta": {},
        "term_start": None,
        "term_end": None,
        "auto_renew_months": None,
        "renewal_notice_days": None,
        "committed_seats": None,
        "seat_price": None,
    }

    discount_confidences: list[float] = []
    discount_provenance: list[str] = []
    tier_confidences: list[float] = []
    tier_provenance: list[str] = []

    for ent in contract.entitlements:
        if ent.term_type == "committed_minimum":
            amount, conversion = _minimum_amount(ent)
            normalized["minimum_schedule"].append({
                "amount": amount,
                **conversion,
                "effective_date": ent.effective_date,
                "provenance": ent.provenance,
                "confidence": float(ent.confidence_score) if amount is not None else 0.0,
                **({"page": ent.page} if ent.page is not None else {}),
                **({"section_ref": ent.section_ref} if ent.section_ref else {}),
                **({"source_file": ent.source_file} if ent.source_file else {}),
                **({"verification": ent.verification} if ent.verification else {}),
            })
        elif ent.term_type in SCALAR_TERM_FIELDS:
            candidates.setdefault(ent.term_type, []).append(ent)
        elif ent.term_type == "overage_tier":
            normalized.setdefault("_overage_tiers", []).append({
                "up_to": ent.tier_up_to,
                "rate": ent.value,
                "provenance": ent.provenance,
                **({"page": ent.page} if ent.page is not None else {}),
                **({"section_ref": ent.section_ref} if ent.section_ref else {}),
            })
            tier_confidences.append(float(ent.confidence_score))
            tier_provenance.append(ent.provenance)
        elif ent.term_type == "discount":
            discount = {
                "name": ent.label or "extracted discount",
                "type": "percent" if 0 < ent.value <= 1 else "amount",
                "value": ent.value,
                "applies_to": "base",
                "starts": ent.start_date,
                "expires": ent.end_date,
                "confidence_score": ent.confidence_score,
                "provenance": ent.provenance,
                **({"page": ent.page} if ent.page is not None else {}),
                **({"section_ref": ent.section_ref} if ent.section_ref else {}),
                **({"verification": ent.verification} if ent.verification else {}),
            }
            if ent.end_date is None and ent.effective_date:
                normalized.setdefault("term_meta", {}).setdefault("discounts", {})["note"] = (
                    f"discount effective_date {ent.effective_date} was set but no end date; "
                    "not treated as an expiry")
            normalized["discounts"].append(discount)
            discount_confidences.append(float(ent.confidence_score))
            discount_provenance.append(ent.provenance)
    resolve_scalar_terms(normalized, candidates)
    _normalize_escalator_date(normalized, candidates)
    detect_term_conflicts(normalized)

    if normalized["minimum_schedule"]:
        # Display value = the entry with the latest effective date (None = earliest).
        schedule = normalized["minimum_schedule"]
        normalized["committed_minimum_monthly"] = max(
            schedule,
            key=lambda e: (e.get("effective_date") is not None,
                           e.get("effective_date") or ""))["amount"]
        normalized["term_meta"]["committed_minimum_monthly"] = minimum_term_meta(schedule)
        for e in schedule:
            e.pop("confidence", None)

    tiers = normalized.pop("_overage_tiers", [])
    if tiers:
        normalized["overage_tiers"] = sorted(
            tiers, key=lambda t: (t["up_to"] is None, t["up_to"] or 0))
        normalized["term_meta"]["overage_tiers"] = {
            "confidence": min(tier_confidences) if tier_confidences else 1.0,
            "provenance": tier_provenance[0] if tier_provenance else "",
            **({"page": tiers[0]["page"]} if tiers and tiers[0].get("page") is not None else {}),
            **({"section_ref": tiers[0]["section_ref"]} if tiers and tiers[0].get("section_ref") else {}),
            **({"source_file": tiers[0]["source_file"]} if tiers and tiers[0].get("source_file") else {}),
        }

    if normalized["discounts"]:
        normalized["term_meta"].setdefault("discounts", {})
        normalized["term_meta"]["discounts"].update({
            "confidence": min(discount_confidences) if discount_confidences else 1.0,
            "provenance": " | ".join(discount_provenance),
        })

    return normalized


def _scalar_candidate_ref(ent: Entitlement) -> dict:
    ref = {"value": ent.effective_date
           if _term_type_of(ent) in ("term_start", "term_end") else ent.value}
    for key in ("page", "section_ref", "source_file", "provenance",
                "verification", "confidence_score", "effective_date"):
        if getattr(ent, key, None) is not None:
            ref["confidence" if key == "confidence_score" else key] = getattr(ent, key)
    return ref


def _term_type_of(ent: Entitlement) -> str:
    return ent.term_type


def resolve_scalar_terms(normalized: dict, candidates: dict[str, list]) -> None:
    """Assign scalar contract fields from extraction candidates, failing
    closed when an agreement states two different values for the same term
    with the same effective date (e.g. 240 seats in §4.1 and 200 seats in
    Exhibit A). Latest-dated wins when dates differ; identical duplicates
    dedupe; conflicting same-date values are flagged on term_conflicts and
    the field stays None so reconciliation skips deterministically."""
    for term_type, ents in candidates.items():
        field, coerce = SCALAR_TERM_FIELDS[term_type]
        meta_by_ent = {id(e): _term_meta(e) for e in ents}
        values = []
        for ent in ents:
            value = ent.effective_date if term_type in ("term_start", "term_end") else ent.value
            values.append((value, ent.effective_date, ent))
        # Dedupe identical (value, effective_date)
        seen = set()
        distinct = []
        for item in values:
            key = (item[0], item[1])
            if key not in seen:
                seen.add(key)
                distinct.append(item)
        distinct_values = {item[0] for item in distinct}
        if len(distinct_values) == 1:
            _v, _e, ent = max(distinct, key=lambda item: (
                item[1] is not None, item[1] or ""))
            _assign_scalar(normalized, field, coerce, _v, term_type, ent, meta_by_ent)
            continue
        if term_type not in ("term_start", "term_end"):
            # Amendment precedence: every entry dated differently → latest wins.
            dated = [item for item in distinct if item[1] is not None]
            dates = {item[1] for item in dated}
            if len(dates) == len(distinct) and len(dates) > 1:
                value, eff, ent = max(dated, key=lambda item: item[1])
                _assign_scalar(normalized, field, coerce, value, term_type, ent, meta_by_ent)
                continue
        # term_start/term_end carry their boundary in effective_date, so any
        # two distinct values disagree with no amendment date to order them —
        # same for same-date/undated scalars. Fail closed.
        eff = distinct[0][1]
        normalized.setdefault("term_conflicts", []).append({
            "term": term_type,
            "effective_date": eff,
            "candidates": [_scalar_candidate_ref(ent)
                           for _v, _e, ent in distinct],
        })
        normalized["term_meta"][field] = {
            "confidence": 0.0,
            "provenance": meta_by_ent[id(distinct[0][2])].get("provenance"),
            "conflict": True,
        }


def _assign_scalar(normalized: dict, field: str, coerce, value, term_type: str,
                   ent: Entitlement, meta_by_ent: dict) -> None:
    if coerce is None:
        normalized[field] = value
    else:
        normalized[field] = coerce(value)
    normalized["term_meta"][field] = meta_by_ent[id(ent)]
    if term_type == "escalator":
        normalized["escalator_effective_date"] = ent.effective_date
        normalized["term_meta"]["escalator_effective_date"] = meta_by_ent[id(ent)]


def minimum_term_meta(schedule: list[dict]) -> dict:
    """term_meta entry for committed_minimum_monthly: provenance and
    verification come from the governing (latest effective date) entry;
    confidence is the minimum across the *given* schedule entries, so
    removed/undated candidates never drag it down."""
    governing = max(schedule,
                    key=lambda e: (e.get("effective_date") is not None,
                                   e.get("effective_date") or ""))
    confidence = min(
        e["confidence"] if e.get("confidence") is not None
        else (e.get("verification") or {}).get("final_confidence", 1.0)
        for e in schedule)
    meta = {"confidence": confidence, "provenance": governing.get("provenance")}
    for key in ("page", "section_ref", "source_file", "verification",
                "source_value", "amount_period", "normalization_formula", "normalization_error"):
        if governing.get(key) is not None:
            meta[key] = governing[key]
    return meta


def _candidate_ref(entry: dict, keys=("amount", "page", "section_ref", "source_file", "provenance", "verification", "confidence")) -> dict:
    return {k: entry[k] for k in keys if entry.get(k) is not None}


def detect_term_conflicts(contract: dict) -> None:
    """Fail-closed guard for the minimum_schedule: conflicting amounts for the
    same effective date are flagged on `term_conflicts`; undated entries that
    compete with dated ones are moved to `unresolved_terms` and removed from
    the schedule so deterministic math never picks an arbitrary value.

    Same amount + same date duplicates are deduped rather than flagged.
    Written generically over minimum_schedule; the emitted term name is
    committed_minimum for every entry.
    """
    schedule = contract.get("minimum_schedule") or []
    if not schedule:
        return
    seen = set()
    deduped = []
    for entry in schedule:
        key = (entry.get("amount"), entry.get("effective_date"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)

    by_date: dict = {}
    for entry in deduped:
        by_date.setdefault(entry.get("effective_date"), []).append(entry)

    conflicts: list[dict] = []
    unresolved: list[dict] = []
    keep: list[dict] = []
    for eff_date, entries in by_date.items():
        if eff_date is None:
            if len(deduped) > len(entries):
                for entry in entries:
                    unresolved.append({
                        "term": "committed_minimum",
                        "reason": "undated_amendment",
                        **_candidate_ref(entry)})
                continue  # removed from the schedule; stays in term_history
            keep.extend(entries)
            continue
        if len({e.get("amount") for e in entries}) > 1:
            conflicts.append({
                "term": "committed_minimum",
                "effective_date": eff_date,
                "candidates": [_candidate_ref(e) for e in entries],
            })
        keep.extend(entries)

    contract["minimum_schedule"] = keep
    # Extend, not overwrite: scalar-term conflicts found during normalization
    # must be preserved; only committed_minimum entries are replaced here.
    contract["term_conflicts"] = [
        c for c in contract.get("term_conflicts") or []
        if c.get("term") != "committed_minimum"] + conflicts
    contract["unresolved_terms"] = [
        u for u in contract.get("unresolved_terms") or []
        if u.get("term") != "committed_minimum"] + unresolved
