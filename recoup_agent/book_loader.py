"""Load the customer book (contracts, usage, invoices) from JSON files.

`recoup_agent/data/{contracts,usage,invoices}.json` may hold either the
reconciliation engine's internal schema or the "clean test set" schema. When all
three files exist they are normalized into the internal schema; otherwise the
synthetic_data constants are used, so the demo always runs.
"""
from __future__ import annotations
import json
from pathlib import Path

from .synthetic_data import all_contracts, USAGE, INVOICES

DATA_DIR = Path(__file__).parent / "data"


def _records(path: Path, wrapper_key: str) -> list[dict] | None:
    if not path.exists():
        return None
    with open(path) as fh:
        data = json.load(fh)
    if isinstance(data, list):
        return data
    return data.get(wrapper_key, [])


def load_book(data_dir: Path = DATA_DIR) -> tuple[list[dict], list[dict], list[dict]]:
    """contracts, usage, invoices in the reconciliation engine's internal schema.
    Uses data_dir JSON files if all three exist, else synthetic_data constants."""
    raw_contracts = _records(data_dir / "contracts.json", "customers")
    raw_usage = _records(data_dir / "usage.json", "usage_records")
    raw_invoices = _records(data_dir / "invoices.json", "invoices")
    if raw_contracts is None or raw_usage is None or raw_invoices is None:
        return all_contracts(), USAGE, INVOICES

    contracts = [c if "customer_name" in c else normalize_contract(c) for c in raw_contracts]
    usage = [u if "units" in u else normalize_usage(u) for u in raw_usage]
    by_id = {c["customer_id"]: c for c in contracts}
    invoices = [
        i if "base_charge" in i
        else normalize_invoice(i, by_id.get(i.get("customer_id")))
        for i in raw_invoices
    ]
    return contracts, usage, invoices


def load_contracts(data_dir: Path = DATA_DIR) -> list[dict]:
    contracts, _, _ = load_book(data_dir)
    return contracts


def book_periods(usage: list[dict], invoices: list[dict]) -> list[str]:
    """Sorted unique periods present in both usage and invoices."""
    usage_periods = {u["period"] for u in usage if u.get("period")}
    invoice_periods = {i["period"] for i in invoices if i.get("period")}
    return sorted(usage_periods & invoice_periods)


def normalize_contract(raw: dict) -> dict:
    """Clean test set contract -> internal schema."""
    tier = raw.get("committed_tier") or {}
    notes = raw.get("notes", "")
    discounts = []
    for d in raw.get("discounts") or []:
        dnotes = d.get("notes", "")
        name = d.get("name") or (dnotes.split(":")[0].strip() if dnotes else d.get("type", ""))
        discounts.append({
            "name": name,
            "type": "percent",
            "value": d.get("amount_pct"),
            "applies_to": "base",
            "starts": d.get("starts"),
            "expires": d.get("expires"),
        })
    esc_month = raw.get("escalator_effective_month")
    contract = {
        "customer_id": raw["customer_id"],
        "customer_name": raw.get("name", raw["customer_id"]),
        "contract_id": f"C-{raw['customer_id'].upper()}-{raw.get('contract_start', '')[:4]}",
        "effective_date": raw.get("contract_start"),
        "committed_minimum_monthly": raw.get("minimum_monthly_commit", tier.get("base_monthly_fee")),
        "included_units": tier.get("included_units"),
        "overage_rate": tier.get("overage_rate_per_unit"),
        "annual_escalator_pct": raw.get("annual_escalator_pct", 0.0),
        "escalator_effective_date": raw.get("escalator_effective_date") or (f"{esc_month}-01" if esc_month else None),
        "discounts": discounts,
        "clauses": {
            "committed_minimum": notes,
            "overage": notes,
            "discount": "; ".join(dn for dn in (d.get("notes", "") for d in raw.get("discounts") or []) if dn),
            "escalator": notes,
        },
    }
    term = raw.get("term") or {}
    seats = raw.get("seats") or {}
    for key, value in (
        ("term_start", term.get("start", raw.get("contract_start"))),
        ("term_end", term.get("end", raw.get("contract_end"))),
        ("auto_renew_months", term.get("auto_renew_months", raw.get("auto_renew_months"))),
        ("renewal_notice_days", term.get("notice_days", raw.get("renewal_notice_days"))),
        ("committed_seats", seats.get("committed", raw.get("committed_seats"))),
        ("seat_price", seats.get("price", raw.get("seat_price"))),
    ):
        if value is not None:
            contract[key] = value
            contract.setdefault("term_meta", {})[key] = {"confidence": 1.0, "provenance": notes}
    if tier.get("overage_tiers"):
        contract["overage_tiers"] = sorted(
            ({"up_to": t.get("up_to"), "rate": t["rate"], "provenance": notes}
             for t in tier["overage_tiers"]),
            key=lambda t: (t["up_to"] is None, t["up_to"] or 0))
        contract.setdefault("term_meta", {})["overage_tiers"] = {
            "confidence": 1.0, "provenance": notes}
    if raw.get("amendments"):
        contract["amendments"] = raw["amendments"]
        schedule = [{
            "amount": tier.get("base_monthly_fee") or raw.get("minimum_monthly_commit"),
            "effective_date": raw.get("contract_start"),
            "provenance": "original term",
        }]
        for amd in raw["amendments"]:
            if "minimum" in (amd.get("change") or "").lower():
                schedule.append({
                    "amount": raw.get("minimum_monthly_commit"),
                    "effective_date": amd.get("effective"),
                    "provenance": amd.get("change"),
                })
        if len(schedule) > 1:
            contract["minimum_schedule"] = schedule
    return contract


def normalize_usage(raw: dict) -> dict:
    """Clean test set usage record -> internal schema."""
    return {
        "customer_id": raw["customer_id"],
        "period": raw["period"],
        "units": raw.get("units_consumed"),
    }


def match_discount(contract_discounts: list[dict], description: str) -> str:
    """Resolve an applied discount's display name against a contract's discounts.
    Case-insensitive containment either direction; falls back to the single
    contract discount, then to the raw description."""
    desc = (description or "").lower()
    for d in contract_discounts:
        name = (d.get("name") or "").lower()
        if name and (name in desc or desc in name):
            return d["name"]
    if len(contract_discounts) == 1:
        return contract_discounts[0]["name"]
    return description


def _match_discount_strict(contract_discounts: list[dict], description: str) -> str | None:
    """Like match_discount but returns None when no contract discount's name
    matches (never falls back to a single discount or the raw description —
    a 'Service credit' line must not be mistaken for a contractual discount)."""
    desc = (description or "").lower()
    for d in contract_discounts:
        name = (d.get("name") or "").lower()
        if name and (name in desc or desc in name):
            return d["name"]
    return None


def normalize_invoice(raw: dict, contract: dict | None) -> dict:
    """Clean test set invoice (line_items) -> internal schema."""
    from .line_roles import SEAT_RE, classify_line

    invoice = {
        "customer_id": raw["customer_id"],
        "period": raw["period"],
        "base_charge": 0.0,
        "overage_charge": 0.0,
        "discounts_applied": [],
        "tax_excluded": 0.0,
        "credits_applied": [],
        "prorated": False,
        "proration_amount": 0.0,
    }
    contract_discounts = (contract or {}).get("discounts") or []
    for item in raw.get("line_items") or []:
        amount = item.get("amount", 0.0)
        description = item.get("description", "")
        role = classify_line(description, amount, contract_discounts=contract_discounts)
        if role == "proration":
            invoice["prorated"] = True
            invoice["proration_amount"] += amount
        elif role == "tax":
            invoice["tax_excluded"] += amount
        elif role == "credit":
            invoice["credits_applied"].append({"description": description, "amount": abs(amount)})
        elif role == "discount":
            matched = match_discount(contract_discounts, description)
            invoice["discounts_applied"].append({"name": matched, "amount": abs(amount)})
        elif role == "overage":
            invoice["overage_charge"] += amount
        else:
            invoice["base_charge"] += amount
        units = item.get("units", item.get("quantity", item.get("qty")))
        if units is not None and SEAT_RE.search(description):
            invoice["seat_units"] = invoice.get("seat_units", 0.0) + float(units)
    if "invoice_id" in raw:
        invoice["invoice_id"] = raw["invoice_id"]
    return invoice
