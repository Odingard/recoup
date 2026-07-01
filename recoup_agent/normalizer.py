from __future__ import annotations

import re

from .ingestion_doc import ContractEntitlements, Entitlement


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "unknown"


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
        "included_units": None,
        "overage_rate": None,
        "discounts": [],
        "annual_escalator_pct": None,
        "escalator_effective_date": None,
        "term_meta": {},
    }

    discount_confidences: list[float] = []
    discount_provenance: list[str] = []

    for ent in contract.entitlements:
        meta = _term_meta(ent)
        if ent.term_type == "committed_minimum":
            normalized["committed_minimum_monthly"] = ent.value
            normalized["term_meta"]["committed_minimum_monthly"] = meta
        elif ent.term_type == "included_units":
            normalized["included_units"] = int(ent.value)
            normalized["term_meta"]["included_units"] = meta
        elif ent.term_type == "overage_rate":
            normalized["overage_rate"] = ent.value
            normalized["term_meta"]["overage_rate"] = meta
        elif ent.term_type == "discount":
            discount = {
                "name": "extracted discount",
                "type": "percent" if 0 < ent.value <= 1 else "amount",
                "value": ent.value,
                "applies_to": "base",
                "expires": ent.effective_date,
                "confidence_score": ent.confidence_score,
                "provenance": ent.provenance,
            }
            normalized["discounts"].append(discount)
            discount_confidences.append(float(ent.confidence_score))
            discount_provenance.append(ent.provenance)
        elif ent.term_type == "escalator":
            normalized["annual_escalator_pct"] = ent.value
            normalized["escalator_effective_date"] = ent.effective_date
            normalized["term_meta"]["annual_escalator_pct"] = meta
            normalized["term_meta"]["escalator_effective_date"] = meta

    if normalized["discounts"]:
        normalized["term_meta"]["discounts"] = {
            "confidence": min(discount_confidences) if discount_confidences else 1.0,
            "provenance": " | ".join(discount_provenance),
        }

    return normalized
