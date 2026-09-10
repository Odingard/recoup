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
        "escalator_effective_date": f"{esc_month}-01" if esc_month else None,
        "discounts": discounts,
        "clauses": {
            "committed_minimum": notes,
            "overage": notes,
            "discount": "; ".join(dn for dn in (d.get("notes", "") for d in raw.get("discounts") or []) if dn),
            "escalator": notes,
        },
    }
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


def normalize_invoice(raw: dict, contract: dict | None) -> dict:
    """Clean test set invoice (line_items) -> internal schema."""
    base_charge = 0.0
    overage_charge = 0.0
    discounts_applied: list[dict] = []
    contract_discounts = (contract or {}).get("discounts") or []
    for item in raw.get("line_items") or []:
        amount = item.get("amount", 0.0)
        description = item.get("description", "")
        desc = description.lower()
        if amount < 0:
            matched = next(
                (d["name"] for d in contract_discounts if d["name"].lower() in desc),
                None,
            )
            if matched is None and len(contract_discounts) == 1:
                matched = contract_discounts[0]["name"]
            discounts_applied.append({"name": matched or description, "amount": abs(amount)})
        elif "overage" in desc:
            overage_charge += amount
        else:
            base_charge += amount
    invoice = {
        "customer_id": raw["customer_id"],
        "period": raw["period"],
        "base_charge": base_charge,
        "overage_charge": overage_charge,
        "discounts_applied": discounts_applied,
    }
    if "invoice_id" in raw:
        invoice["invoice_id"] = raw["invoice_id"]
    return invoice
