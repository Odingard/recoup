from __future__ import annotations

from .identity import canonical_key
from .ingestion_doc import ContractEntitlements, Entitlement


def _slugify(value: str) -> str:
    return canonical_key(value) or "unknown"


def _term_meta(ent: Entitlement) -> dict:
    return {
        "confidence": float(ent.confidence_score),
        "provenance": ent.provenance,
    }


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
            })
            normalized["term_meta"].setdefault("committed_minimum_monthly", meta)
            if ent.confidence_score < normalized["term_meta"]["committed_minimum_monthly"]["confidence"]:
                normalized["term_meta"]["committed_minimum_monthly"] = meta
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

    if normalized["minimum_schedule"]:
        # Display value = the entry with the latest effective date (None = earliest).
        latest = max(normalized["minimum_schedule"],
                     key=lambda e: (e.get("effective_date") is not None, e.get("effective_date") or ""))
        normalized["committed_minimum_monthly"] = latest["amount"]

    tiers = normalized.pop("_overage_tiers", [])
    if tiers:
        normalized["overage_tiers"] = sorted(
            tiers, key=lambda t: (t["up_to"] is None, t["up_to"] or 0))
        normalized["term_meta"]["overage_tiers"] = {
            "confidence": min(tier_confidences) if tier_confidences else 1.0,
            "provenance": tier_provenance[0] if tier_provenance else "",
        }

    if normalized["discounts"]:
        normalized["term_meta"].setdefault("discounts", {})
        normalized["term_meta"]["discounts"].update({
            "confidence": min(discount_confidences) if discount_confidences else 1.0,
            "provenance": " | ".join(discount_provenance),
        })

    return normalized
