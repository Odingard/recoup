"""Recoup engine + a standalone deterministic demo.

`python -m recoup_agent.pipeline` runs the whole reconciliation -> draft -> audit
loop with NO Gemini and NO credentials. It's your always-works demo backbone and
the set of functions the ADK tools wrap.
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .reconciliation import reconcile
from .billing.connector_keys import resolve_connector_key
from .synthetic_data import all_contracts, USAGE, INVOICES
from . import db

DATA_DIR = Path(__file__).parent / "data"


def _load_book(account_id: str | None = None) -> tuple[list[dict], list[dict], list[dict]]:
    if account_id is None:
        return all_contracts(), USAGE, INVOICES
    return db.get_all_contracts(account_id), db.get_all_usage(account_id), db.get_all_invoices(account_id)


def _load_contracts(account_id: str | None = None) -> list[dict]:
    if account_id is None:
        return all_contracts()
    return db.get_all_contracts(account_id)


def _selected_billing_provider(account_id: str | None, billing_provider=None):
    if billing_provider is not None:
        return billing_provider
    if os.getenv("RECOUP_BILLING_SOURCE", "").lower() == "stripe":
        from .billing.stripe_provider import StripeBillingProvider
        return StripeBillingProvider(api_key=resolve_connector_key(account_id))
    return None


def compute_findings_and_review(
    period: str = "2026-06",
    account_id: str | None = None,
    billing_provider=None,
) -> tuple[list[dict], list[dict]]:
    provider = _selected_billing_provider(account_id, billing_provider) if account_id is not None else None
    contracts = _load_contracts(account_id)
    usage = invoices = None
    if provider is None:
        _, usage_list, invoices_list = _load_book(account_id)
        usage = {(u["customer_id"], u["period"]): u for u in usage_list}
        invoices = {(i["customer_id"], i["period"]): i for i in invoices_list}

    findings: list[dict] = []
    needs_review: list[dict] = []
    for c in contracts:
        key = (c["customer_id"], period)
        if provider is not None:
            from .billing.stripe_provider import map_stripe_billing_to_reconcile_inputs
            normalized_usage = provider.get_usage(c["customer_id"], period)
            normalized_invoices = provider.get_invoices(c["customer_id"], period)
            usage_dict, invoice_dict, review_items = map_stripe_billing_to_reconcile_inputs(
                c["customer_id"], c["customer_name"], period, normalized_usage, normalized_invoices
            )
            needs_review.extend(review_items)
            findings.extend(reconcile(c, usage_dict, invoice_dict, period, needs_review=needs_review))
        else:
            if key not in usage or key not in invoices:
                continue  # missing billing data this period
            findings.extend(reconcile(c, usage[key], invoices[key], period, needs_review=needs_review))

    findings.sort(key=lambda f: f["monthly_recoverable"], reverse=True)
    return findings, needs_review


def compute_findings(period: str = "2026-06", account_id: str | None = None, billing_provider=None) -> list[dict]:
    findings, _ = compute_findings_and_review(period, account_id=account_id, billing_provider=billing_provider)
    return findings


def build_corrective_memo(customer_id: str, findings: list[dict], period: str = "2026-06"):
    total = round(sum(f["monthly_recoverable"] for f in findings), 2)
    name = findings[0]["customer_name"]
    lines = [f"CORRECTIVE INVOICE - {name} - billing period {period}", "-" * 58]
    for f in findings:
        lines.append(f"  [{f['finding_id']}] {f['title']}: ${f['monthly_recoverable']:,.2f}")
        lines.append(f"      {f['detail']}")
    lines += ["-" * 58,
              f"  Total recoverable this period: ${total:,.2f}",
              f"  Annualized: ${total*12:,.2f}",
              "  Status: DRAFT - pending human approval."]
    return "\n".join(lines), total


def append_audit(entry: dict) -> None:
    entry = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DATA_DIR / "audit_log.jsonl", "a") as fh:
        fh.write(json.dumps(entry) + "\n")


def main() -> None:
    period = "2026-06"
    findings = compute_findings(period)
    total = sum(f["monthly_recoverable"] for f in findings)

    print(f"\nRECOUP - revenue leakage reconciliation - period {period}")
    print("=" * 60)
    by_customer: dict[str, list[dict]] = {}
    for f in findings:
        by_customer.setdefault(f["customer_id"], []).append(f)
    for cid, fs in by_customer.items():
        memo, _ = build_corrective_memo(cid, fs, period)
        print("\n" + memo)

    print("\n" + "=" * 60)
    print(f"BOOK TOTAL recoverable / month: ${total:,.2f}  (annualized ${total*12:,.2f})")
    print("\nHuman approval gate (demo): findings await sign-off before any invoice issues.")
    for f in findings:
        append_audit({"event": "submitted_for_approval",
                      "finding_id": f["finding_id"], "amount": f["monthly_recoverable"]})
    print(f"Audit log written to {DATA_DIR / 'audit_log.jsonl'}")


if __name__ == "__main__":
    main()
