"""ADK function tools.

Each agent calls these. Structured findings flow between agents through ADK
session state (tool_context.state); the LLMs only narrate and reason. A module
fallback dict keeps the tools runnable even if state access differs by ADK version.
"""
from __future__ import annotations

from .pipeline import load_data, compute_findings, build_corrective_memo, append_audit

PERIOD = "2026-06"
_fallback_state: dict = {}


def _state(tool_context):
    try:
        return tool_context.state
    except Exception:
        return _fallback_state


def list_contracts(tool_context) -> dict:
    """Load the customer contract and billing book; returns each customer's key billing terms."""
    contracts = load_data()["contracts"]
    summary = [{
        "customer_id": c["customer_id"], "customer_name": c["customer_name"],
        "committed_minimum_monthly": c.get("committed_minimum_monthly"),
        "included_units": c.get("included_units"), "overage_rate": c.get("overage_rate"),
        "discounts": [d["name"] for d in c.get("discounts", [])],
        "annual_escalator_pct": c.get("annual_escalator_pct"),
    } for c in contracts]
    _state(tool_context)["_loaded"] = True
    return {"period": PERIOD, "customers": summary}


def run_reconciliation(tool_context) -> dict:
    """Reconcile entitlements against billing. Returns findings and total recoverable (deterministic)."""
    findings = compute_findings(PERIOD)
    _state(tool_context)["findings"] = findings
    by_customer: dict[str, float] = {}
    for f in findings:
        by_customer[f["customer_name"]] = by_customer.get(f["customer_name"], 0.0) + f["monthly_recoverable"]
    total = round(sum(f["monthly_recoverable"] for f in findings), 2)
    return {
        "total_monthly_recoverable": total,
        "annualized_recoverable": round(total * 12, 2),
        "finding_count": len(findings),
        "by_customer": {k: round(v, 2) for k, v in by_customer.items()},
        "findings": findings,
    }


def get_findings(tool_context) -> dict:
    """Return the reconciliation findings produced earlier in the pipeline."""
    findings = _state(tool_context).get("findings") or compute_findings(PERIOD)
    _state(tool_context)["findings"] = findings
    return {"findings": findings}


def lookup_contract_clause(customer_id: str, clause_ref: str, tool_context) -> dict:
    """Return the governing contract clause text for a finding.

    Demo uses an in-record clause lookup. In production, swap this for a Vertex AI
    Search query against a data store built from the contract corpus (true RAG).
    """
    for c in load_data()["contracts"]:
        if c["customer_id"] == customer_id:
            clause = c.get("clauses", {}).get(clause_ref) or "Clause text not found."
            return {"customer_id": customer_id, "clause_ref": clause_ref, "clause_text": clause}
    return {"customer_id": customer_id, "clause_ref": clause_ref, "clause_text": "Customer not found."}


def draft_corrective_invoice(customer_id: str, tool_context) -> dict:
    """Draft a corrective invoice / credit memo for one customer's findings."""
    findings = _state(tool_context).get("findings") or compute_findings(PERIOD)
    cust = [f for f in findings if f["customer_id"] == customer_id]
    if not cust:
        return {"customer_id": customer_id, "memo": "No recoverable findings for this customer."}
    memo, total = build_corrective_memo(customer_id, cust, PERIOD)
    _state(tool_context).setdefault("drafts", {})[customer_id] = {
        "memo": memo, "total": total, "finding_ids": [f["finding_id"] for f in cust]}
    return {"customer_id": customer_id, "total_recoverable": total, "memo": memo}


def submit_for_approval(tool_context) -> dict:
    """Place all drafted corrective invoices into the human approval queue (status: pending)."""
    findings = _state(tool_context).get("findings") or []
    queue = []
    for f in findings:
        current_status = f.get("status")
        if current_status not in ("approved", "rejected"):
            f["status"] = "pending_approval"
            queue.append({"finding_id": f["finding_id"], "customer_name": f["customer_name"],
                          "monthly_recoverable": f["monthly_recoverable"], "status": "pending_approval"})
            append_audit({"event": "submitted_for_approval",
                          "finding_id": f["finding_id"], "amount": f["monthly_recoverable"]})
        else:
            queue.append({"finding_id": f["finding_id"], "customer_name": f["customer_name"],
                          "monthly_recoverable": f["monthly_recoverable"], "status": current_status})
    _state(tool_context)["findings"] = findings
    return {"awaiting_approval": queue,
            "message": "All items require explicit human approval before any invoice is issued."}


def record_approval_decision(finding_id: str, approved: bool, tool_context) -> dict:
    """Record a human's approve/reject decision. Call ONLY when the human explicitly decides."""
    findings = _state(tool_context).get("findings") or []
    status = "approved" if approved else "rejected"
    hit = None
    for f in findings:
        if f["finding_id"] == finding_id:
            f["status"] = status
            hit = f
    append_audit({"event": "approval_decision", "finding_id": finding_id, "decision": status})
    _state(tool_context)["findings"] = findings
    if not hit:
        return {"finding_id": finding_id, "status": "not_found"}
    return {"finding_id": finding_id, "status": status,
            "message": f"{finding_id} {status}; decision written to the audit log."}
