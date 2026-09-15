from __future__ import annotations

from .identity import canonical_key
from .ingestion_doc import ContractEntitlements, Entitlement


def _slugify(value: str) -> str:
    return canonical_key(value) or "unknown"


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


def normalize_contract_entitlements(contract: ContractEntitlements) -> dict:
    normalized = {
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
        meta = _term_meta(ent)
        if ent.term_type == "committed_minimum":
            normalized["minimum_schedule"].append({
                "amount": ent.value,
                "effective_date": ent.effective_date,
                "provenance": ent.provenance,
                "confidence": float(ent.confidence_score),
                **({"page": ent.page} if ent.page is not None else {}),
                **({"section_ref": ent.section_ref} if ent.section_ref else {}),
                **({"source_file": ent.source_file} if ent.source_file else {}),
                **({"verification": ent.verification} if getattr(ent, "verification", None) else {}),
            })
        elif ent.term_type == "included_units":
            normalized["included_units"] = int(ent.value)
            normalized["term_meta"]["included_units"] = meta
        elif ent.term_type == "overage_rate":
            normalized["overage_rate"] = ent.value
            normalized["term_meta"]["overage_rate"] = meta
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
        elif ent.term_type == "escalator":
            normalized["annual_escalator_pct"] = ent.value
            normalized["escalator_effective_date"] = ent.effective_date
            normalized["term_meta"]["annual_escalator_pct"] = meta
            normalized["term_meta"]["escalator_effective_date"] = meta
        elif ent.term_type in ("term_start", "term_end"):
            normalized[ent.term_type] = ent.effective_date
            normalized["term_meta"][ent.term_type] = meta
        elif ent.term_type == "auto_renewal":
            normalized["auto_renew_months"] = int(ent.value)
            normalized["term_meta"]["auto_renew_months"] = meta
        elif ent.term_type == "renewal_notice_days":
            normalized["renewal_notice_days"] = int(ent.value)
            normalized["term_meta"]["renewal_notice_days"] = meta
        elif ent.term_type == "committed_seats":
            normalized["committed_seats"] = int(ent.value)
            normalized["term_meta"]["committed_seats"] = meta
        elif ent.term_type == "seat_price":
            normalized["seat_price"] = ent.value
            normalized["term_meta"]["seat_price"] = meta

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
    for key in ("page", "section_ref", "source_file", "verification"):
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
    contract["term_conflicts"] = conflicts
    contract["unresolved_terms"] = unresolved
