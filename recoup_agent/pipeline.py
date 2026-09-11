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
from .book_loader import load_book, load_contracts, book_periods
from . import db

DATA_DIR = Path(__file__).parent / "data"


def _load_book(account_id: str | None = None) -> tuple[list[dict], list[dict], list[dict]]:
    if account_id is None:
        return load_book()
    return db.get_all_contracts(account_id), db.get_all_usage(account_id), db.get_all_invoices(account_id)


def _load_contracts(account_id: str | None = None) -> list[dict]:
    if account_id is None:
        return load_contracts()
    return db.get_all_contracts(account_id)


def _selected_billing_provider(account_id: str | None, billing_provider=None):
    if billing_provider is not None:
        return billing_provider
    if os.getenv("RECOUP_BILLING_SOURCE", "").lower() == "stripe":
        key = resolve_connector_key(account_id)
        if not key:
            return None
        from .billing.stripe_provider import StripeBillingProvider
        return StripeBillingProvider(api_key=key)
    return None


def compute_findings_and_review(
    period: str = "2026-06",
    account_id: str | None = None,
    billing_provider=None,
    book: tuple[list[dict], list[dict], list[dict]] | None = None,
) -> tuple[list[dict], list[dict]]:
    provider = _selected_billing_provider(account_id, billing_provider) if account_id is not None else None
    if book is not None:
        contracts, usage_list, invoices_list = book
    else:
        contracts, usage_list, invoices_list = _load_book(account_id)
    # Uploaded records win over the live connector for a given customer/period.
    usage = {(u["customer_id"], u["period"]): u for u in usage_list}
    invoices = {(i["customer_id"], i["period"]): i for i in invoices_list}

    findings: list[dict] = []
    needs_review: list[dict] = []
    for c in contracts:
        key = (c["customer_id"], period)
        if key in usage and key in invoices:
            findings.extend(reconcile(c, usage[key], invoices[key], period, needs_review=needs_review))
            continue
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
            missing_usage = key not in usage
            missing_invoice = key not in invoices
            if missing_usage or missing_invoice:
                has_any = any(u["customer_id"] == c["customer_id"] for u in usage_list) or \
                          any(i["customer_id"] == c["customer_id"] for i in invoices_list)
                if not has_any:
                    needs_review.append({
                        "customer_id": c["customer_id"], "customer_name": c["customer_name"],
                        "term": "billing_data",
                        "reason": "No billing or usage data on file for this customer",
                        "suggested_action": "Confirm the customer appears in the billing and usage exports under a recognizable name",
                    })
                else:
                    missing = "/".join(part for part, miss in
                                       (("usage", missing_usage), ("invoice", missing_invoice)) if miss)
                    needs_review.append({
                        "customer_id": c["customer_id"], "customer_name": c["customer_name"],
                        "term": "billing_data",
                        "reason": f"No {missing} data found for this customer in the billing period",
                        "suggested_action": "Confirm the customer appears in the billing and usage exports under a recognizable name",
                    })
                continue
            findings.extend(reconcile(c, usage[key], invoices[key], period, needs_review=needs_review))

    findings.sort(key=lambda f: f["monthly_recoverable"], reverse=True)
    return findings, needs_review


def run_book(contracts: list[dict], usage_list: list[dict], invoices_list: list[dict],
             seed_review: list[dict] = ()) -> tuple[dict[str, list[dict]], list[dict]]:
    """Reconcile every period present in the book; returns findings_by_period and
    a combined needs_review list (identical entries deduped)."""
    findings_by_period: dict[str, list[dict]] = {}
    needs_review: list[dict] = list(seed_review)
    for period in book_periods(usage_list, invoices_list):
        findings, review = compute_findings_and_review(period, book=(contracts, usage_list, invoices_list))
        findings_by_period[period] = findings
        needs_review.extend(review)
    seen: set[str] = set()
    deduped: list[dict] = []
    for item in needs_review:
        key = json.dumps(item, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return findings_by_period, deduped


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
    import argparse

    parser = argparse.ArgumentParser(description="Recoup revenue leakage reconciliation demo")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="Directory containing contracts/usage/invoices JSON (clean schema or internal)")
    parser.add_argument("--dir", type=Path, default=None,
                        help="Directory of contract documents + billing/usage CSVs (messy book)")
    parser.add_argument("--report-html", type=Path, default=None, help="Write the audit report HTML here")
    parser.add_argument("--report-pdf", type=Path, default=None, help="Write the audit report PDF here")
    parser.add_argument("--report-json", type=Path, default=None, help="Write the audit report JSON here")
    args = parser.parse_args()

    seed_review: list[dict] = []
    if args.dir is not None:
        from .ingest_dir import load_book_from_dir
        contracts, usage, invoices, seed_review = load_book_from_dir(args.dir)
    else:
        contracts, usage, invoices = load_book(args.data_dir) if args.data_dir else load_book()

    findings_by_period, needs_review = run_book(contracts, usage, invoices, seed_review)
    periods = list(findings_by_period) or ["2026-06"]
    grand_total = 0.0

    for period in periods:
        findings = findings_by_period.get(period, [])
        total = sum(f["monthly_recoverable"] for f in findings)
        grand_total += total

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

        for f in findings:
            append_audit({"event": "submitted_for_approval",
                          "finding_id": f["finding_id"], "amount": f["monthly_recoverable"]})

    if len(periods) > 1:
        print(f"\nALL PERIODS ({periods[0]}..{periods[-1]}) total recoverable: ${grand_total:,.2f}")

    print(f"\nNEEDS REVIEW ({len(needs_review)})")
    for item in needs_review:
        name = item.get("customer_name") or item.get("customer_id") or "?"
        print(f"  - {name} | {item.get('term')}: {item.get('reason')}")

    if args.report_html or args.report_pdf or args.report_json:
        from .report import build_report, render_html, render_pdf
        report = build_report(findings_by_period, needs_review, contracts)
        if args.report_json:
            args.report_json.parent.mkdir(parents=True, exist_ok=True)
            args.report_json.write_text(json.dumps(report, indent=2))
            print(f"Report JSON written to {args.report_json}")
        if args.report_html:
            args.report_html.parent.mkdir(parents=True, exist_ok=True)
            args.report_html.write_text(render_html(report))
            print(f"Report HTML written to {args.report_html}")
        if args.report_pdf:
            args.report_pdf.parent.mkdir(parents=True, exist_ok=True)
            args.report_pdf.write_bytes(render_pdf(report))
            print(f"Report PDF written to {args.report_pdf}")

    print("\nHuman approval gate (demo): findings await sign-off before any invoice issues.")
    print(f"Audit log written to {DATA_DIR / 'audit_log.jsonl'}")


if __name__ == "__main__":
    main()
