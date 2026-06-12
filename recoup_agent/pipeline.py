"""Recoup engine + a standalone deterministic demo.

`python -m recoup_agent.pipeline` runs the whole reconciliation -> draft -> audit
loop with NO Gemini and NO credentials. It's your always-works demo backbone and
the set of functions the ADK tools wrap.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

from .reconciliation import reconcile
from .billing.csv_provider import CSVBillingProvider
from .ingestion_doc import extract_entitlements
import os

from . import db

def compute_findings(period: str = "2026-06") -> list[dict]:
    # Fetch live data from Firestore instead of mock JSON files
    contracts = db.get_all_contracts()
    usage_list = db.get_all_usage()
    invoices_list = db.get_all_invoices()
    
    usage = {(u["customer_id"], u["period"]): u for u in usage_list}
    invoices = {(i["customer_id"], i["period"]): i for i in invoices_list}
    
    findings: list[dict] = []
    for c in contracts:
        key = (c["customer_id"], period)
        if key not in usage or key not in invoices:
            continue  # missing billing data this period
        findings.extend(reconcile(c, usage[key], invoices[key], period))
    
    findings.sort(key=lambda f: f["monthly_recoverable"], reverse=True)
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
