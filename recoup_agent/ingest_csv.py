"""Flexible CSV ingestion for billing and usage exports.

Column names and date formats vary wildly between exports; this module maps
whatever it finds onto the reconciliation engine's internal schema and surfaces
unresolvable customers as needs_review instead of crashing or silently zeroing.
"""
from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

from .book_loader import match_discount
from .line_roles import classify_line
from .identity import CustomerResolver


class IngestError(ValueError):
    pass


COLUMN_ALIASES = {
    "customer":    ["customer_id", "customer", "customer_name", "account", "account_name", "account_id",
                    "client", "client_name", "contactname", "contact_name", "customer_name_", "display_name", "name"],
    "period":      ["period", "month", "billing_period", "billing_month"],
    "period_start": ["period_start", "start", "start_date", "invoice_date", "date", "created", "service_start",
                     "invoicedate", "txn_date", "transaction_date", "period_start_utc", "created_utc"],
    # 'subtotal'/'net_amount' rank above 'total' so tax-inclusive totals lose.
    "amount":      ["amount", "amount_usd", "amount_billed", "subtotal", "net_amount",
                    "lineamount", "line_amount_", "amount_line", "total", "total_billed", "amount_due", "line_amount"],
    "description": ["description", "line_item", "item", "product", "memo", "plan",
                    "memo_description", "product_service", "line_description", "item_description", "description_"],
    "units":       ["units", "qty", "quantity", "units_consumed", "total_units", "usage", "value",
                    "quantity_", "usage_quantity", "aggregated_usage"],
    "invoice_id":  ["invoice", "invoice_id", "invoice_number", "id", "number",
                    "invoicenumber", "num", "invoice_number_", "doc_number"],
    "metric":      ["metric", "meter", "usage_type", "unit"],
    "status":      ["status", "state"],
}

DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%b %d %Y", "%B %d %Y", "%b %d, %Y",
                "%B %d, %Y", "%d %b %Y", "%Y-%m", "%Y/%m/%d", "%m/%d/%y"]

_SKIP_STATUSES = {"void", "draft", "uncollectible"}


def parse_period(value: str, *, where: str) -> str:
    """Parse a date/period string into 'YYYY-MM'."""
    value = (value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m")
        except ValueError:
            continue
    raise IngestError(
        f"{where}: unrecognised date '{value}'. Accepted formats: "
        "2026-06-01, 06/01/2026, Jun 1 2026, 2026-06")


def _norm_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")


def resolve_columns(header: list[str], required: list[str], optional: list[str],
                    *, where: str) -> dict[str, str]:
    """Map logical roles to actual column names; raise IngestError on missing required."""
    lookup: dict[str, str] = {}
    for col in header:
        lookup[_norm_header(col)] = col
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for role in required + optional:
        for alias in COLUMN_ALIASES.get(role, [role]):
            if _norm_header(alias) in lookup:
                resolved[role] = lookup[_norm_header(alias)]
                break
        else:
            if role in required:
                missing.append(role)
    if missing:
        raise IngestError(
            f"{where}: missing required column(s) {missing}. "
            f"Found columns: {header}. "
            f"Accepted aliases: {', '.join(f'{m}={COLUMN_ALIASES.get(m)}' for m in missing)}")
    return resolved


def _parse_amount(value: str, *, where: str, row: int) -> float:
    raw = (value or "").strip()
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()").replace("$", "").replace(",", "").strip()
    try:
        amount = float(raw)
    except ValueError:
        raise IngestError(f"{where}: row {row}: unparseable amount '{value}'")
    return -amount if negative else amount


def _row_period(row: dict, cols: dict[str, str], *, where: str, rownum: int) -> str:
    if "period" in cols and row.get(cols["period"]):
        return parse_period(row[cols["period"]], where=f"{where} row {rownum}")
    if "period_start" in cols and row.get(cols["period_start"]):
        return parse_period(row[cols["period_start"]], where=f"{where} row {rownum}")
    raise IngestError(f"{where}: row {rownum}: no period or start date")


def _unresolved_review(resolver: CustomerResolver, label: str) -> dict:
    return {
        "customer_id": None,
        "customer_name": label,
        "term": "customer_identity",
        "reason": resolver.explain(label),
        "suggested_action": "Add the customer's contract or map this billing name to an existing customer",
    }


def _read_rows(path, *, where: str) -> tuple[list[str], list[dict]]:
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        rows = [r for r in reader if any((v or "").strip() for v in r.values())]
    if not reader.fieldnames or not rows:
        raise IngestError(f"{where}: no data rows")
    return list(reader.fieldnames), rows


def load_invoices_csv(path, resolver: CustomerResolver) -> tuple[list[dict], list[dict]]:
    """Stripe-style line-item export -> internal invoice dicts + needs_review."""
    where = str(path)
    header, rows = _read_rows(path, where=where)
    cols = resolve_columns(header, required=["customer", "amount"],
                           optional=["period", "period_start", "description", "invoice_id", "status"],
                           where=where)
    if "period" not in cols and "period_start" not in cols:
        raise IngestError(
            f"{where}: missing required column for billing period. Found columns: {header}. "
            f"Accepted aliases: period={COLUMN_ALIASES['period']} or period_start={COLUMN_ALIASES['period_start']}")

    invoices: dict[tuple[str, str], dict] = {}
    needs_review: list[dict] = []
    seen_unresolved: set[str] = set()
    discounts_by_cid = {c["customer_id"]: (c.get("discounts") or []) for c in resolver.contracts}

    for idx, row in enumerate(rows, start=2):
        status = (row.get(cols["status"], "") if "status" in cols else "").strip().lower()
        label = (row.get(cols["customer"]) or "").strip()
        if status in _SKIP_STATUSES:
            needs_review.append({
                "customer_id": resolver.resolve(label),
                "customer_name": label,
                "term": "invoice_status",
                "reason": f"Invoice line skipped because its status is '{status}'",
                "suggested_action": "Confirm whether this invoice should be re-issued or excluded",
            })
            continue
        cid = resolver.resolve(label)
        if cid is None:
            if label not in seen_unresolved:
                seen_unresolved.add(label)
                needs_review.append(_unresolved_review(resolver, label))
            continue
        period = _row_period(row, cols, where=where, rownum=idx)
        amount = _parse_amount(row.get(cols["amount"], ""), where=where, row=idx)
        description = (row.get(cols["description"], "") if "description" in cols else "") or ""

        inv = invoices.setdefault((cid, period), {
            "customer_id": cid, "period": period,
            "base_charge": 0.0, "overage_charge": 0.0,
            "discounts_applied": [], "amount_billed": 0.0,
            "tax_excluded": 0.0, "credits_applied": [],
            "prorated": False, "proration_amount": 0.0,
        })
        if "invoice_id" in cols and row.get(cols["invoice_id"]) and "invoice_id" not in inv:
            inv["invoice_id"] = row[cols["invoice_id"]]
        inv["amount_billed"] += amount
        role = classify_line(description, amount,
                             contract_discounts=discounts_by_cid.get(cid, []))
        if role == "proration":
            inv["prorated"] = True
            inv["proration_amount"] += amount
        elif role == "tax":
            inv["tax_excluded"] += amount
        elif role == "credit":
            inv["credits_applied"].append({"description": description, "amount": abs(amount)})
        elif role == "discount":
            name = match_discount(discounts_by_cid.get(cid, []), description)
            inv["discounts_applied"].append({"name": name, "amount": abs(amount)})
        elif role == "overage":
            inv["overage_charge"] += amount
        else:
            inv["base_charge"] += amount

    return list(invoices.values()), needs_review


def load_usage_csv(path, resolver: CustomerResolver) -> tuple[list[dict], list[dict]]:
    """Metering export -> internal usage dicts + needs_review."""
    where = str(path)
    header, rows = _read_rows(path, where=where)
    cols = resolve_columns(header, required=["customer", "units"],
                           optional=["period", "period_start", "metric"],
                           where=where)
    if "period" not in cols and "period_start" not in cols:
        raise IngestError(
            f"{where}: missing required column for usage period. Found columns: {header}. "
            f"Accepted aliases: period={COLUMN_ALIASES['period']} or period_start={COLUMN_ALIASES['period_start']}")

    per_customer_metrics: dict[str, set[str]] = {}
    usage: dict[tuple[str, str], dict] = {}
    needs_review: list[dict] = []
    seen_unresolved: set[str] = set()

    for idx, row in enumerate(rows, start=2):
        label = (row.get(cols["customer"]) or "").strip()
        cid = resolver.resolve(label)
        if cid is None:
            if label not in seen_unresolved:
                seen_unresolved.add(label)
                needs_review.append(_unresolved_review(resolver, label))
            continue
        period = _row_period(row, cols, where=where, rownum=idx)
        try:
            units = float((row.get(cols["units"]) or "").replace(",", "").strip())
        except ValueError:
            raise IngestError(f"{where}: row {idx}: unparseable units '{row.get(cols['units'])}'")
        metric = (row.get(cols["metric"], "") if "metric" in cols else "") or ""
        if metric:
            per_customer_metrics.setdefault(cid, set()).add(metric)
        rec = usage.setdefault((cid, period), {
            "customer_id": cid, "period": period, "units": 0.0, "_metrics": {}})
        rec["units"] += units
        rec["_metrics"][metric] = rec["_metrics"].get(metric, 0.0) + units

    for cid, metrics in per_customer_metrics.items():
        if len(metrics) > 1:
            needs_review.append({
                "customer_id": cid, "customer_name": cid,
                "term": "usage_metric",
                "reason": f"Multiple usage metrics found ({', '.join(sorted(metrics))}); summed the most common",
                "suggested_action": "Confirm which meter maps to the contract's billable unit",
            })
    for rec in usage.values():
        metrics = rec.pop("_metrics")
        if len(metrics) > 1:
            top = max(metrics.items(), key=lambda kv: kv[1])[0]
            rec["units"] = metrics[top]
        if float(rec["units"]).is_integer():
            rec["units"] = int(rec["units"])
    return list(usage.values()), needs_review


def classify_csv(path) -> str | None:
    """'invoices' if an amount column exists, 'usage' if a units column exists, else None."""
    with open(path, newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
    if not header:
        return None
    names = {_norm_header(c) for c in header}
    if any(_norm_header(a) in names for a in COLUMN_ALIASES["amount"]):
        return "invoices"
    if any(_norm_header(a) in names for a in COLUMN_ALIASES["units"]):
        return "usage"
    return None
