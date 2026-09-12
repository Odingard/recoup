"""ADK function tools.

Each agent calls these. Structured findings flow between agents through ADK
session state (tool_context.state); the LLMs only narrate and reason. A module
fallback dict keeps the tools runnable even if state access differs by ADK version.
"""
from __future__ import annotations

from .pipeline import compute_findings, compute_findings_and_review, build_corrective_memo, append_audit
from .book_loader import load_contracts
from . import vertex_search
from . import db

PERIOD = "2026-06"
_fallback_state: dict = {}

# Maps a finding's clause_ref to natural-language search terms for Vertex AI Search.
CLAUSE_TOPIC = {
    "committed_minimum": "committed monthly minimum platform fee",
    "overage": "usage overage rate per unit above included units",
    "discount": "promotional discount and its expiry date",
    "escalator": "annual price escalator increase",
}


def _state(tool_context):
    try:
        return tool_context.state
    except Exception:
        return _fallback_state


def _period_and_account(tool_context) -> tuple[str, str | None]:
    state = _state(tool_context)
    return state.get("period", PERIOD), state.get("account_id")


def list_contracts(tool_context) -> dict:
    """Load the customer contract and billing book; returns each customer's key billing terms."""
    period, account_id = _period_and_account(tool_context)
    contracts = db.get_all_contracts(account_id) if account_id is not None else load_contracts()
    summary = [{
        "customer_id": c["customer_id"], "customer_name": c["customer_name"],
        "committed_minimum_monthly": c.get("committed_minimum_monthly"),
        "included_units": c.get("included_units"), "overage_rate": c.get("overage_rate"),
        "discounts": [d["name"] for d in c.get("discounts", [])],
        "annual_escalator_pct": c.get("annual_escalator_pct"),
    } for c in contracts]
    _state(tool_context)["_loaded"] = True
    return {"period": period, "customers": summary}


def run_reconciliation(tool_context) -> dict:
    """Reconcile entitlements against billing. Returns findings and total recoverable (deterministic)."""
    period, account_id = _period_and_account(tool_context)
    findings, needs_review = compute_findings_and_review(period, account_id=account_id)
    state = _state(tool_context)
    state["findings"] = findings
    state["needs_review"] = needs_review
    by_customer: dict[str, float] = {}
    for f in findings:
        by_customer[f["customer_name"]] = by_customer.get(f["customer_name"], 0.0) + f["monthly_recoverable"]
    total = round(sum(f["monthly_recoverable"] for f in findings), 2)
    return {
        "total_monthly_recoverable": total,
        "annualized_recoverable": round(total * 12, 2),
        "finding_count": len(findings),
        "needs_review_count": len(needs_review),
        "needs_review": needs_review,
        "by_customer": {k: round(v, 2) for k, v in by_customer.items()},
        "findings": findings,
    }


def get_findings(tool_context) -> dict:
    """Return the reconciliation findings produced earlier in the pipeline."""
    period, account_id = _period_and_account(tool_context)
    findings = _state(tool_context).get("findings") or compute_findings(period, account_id=account_id)
    _state(tool_context)["findings"] = findings
    return {"findings": findings}


def lookup_contract_clause(customer_id: str, clause_ref: str, tool_context) -> dict:
    """Return the governing contract clause text for a finding.

    Uses Vertex AI Search RAG when VERTEX_AI_SEARCH_ENGINE_ID is configured; otherwise
    falls back to the local clause record so the demo always runs.
    """
    _, account_id = _period_and_account(tool_context)
    contracts = db.get_all_contracts(account_id) if account_id is not None else load_contracts()
    contract = next((c for c in contracts if c["customer_id"] == customer_id), None)
    local_clause = (contract or {}).get("clauses", {}).get(clause_ref) or "Clause text not found."
    customer_name = (contract or {}).get("customer_name", customer_id)

    if vertex_search.is_enabled():
        topic = CLAUSE_TOPIC.get(clause_ref, clause_ref)
        retrieved = vertex_search.search_clause(f"{customer_name} {topic}")
        if retrieved:
            return {"customer_id": customer_id, "clause_ref": clause_ref,
                    "clause_text": retrieved, "source": "vertex_ai_search"}
    return {"customer_id": customer_id, "clause_ref": clause_ref,
            "clause_text": local_clause, "source": "local"}


def draft_corrective_invoice(customer_id: str, tool_context) -> dict:
    """Draft a corrective invoice / credit memo for one customer's findings."""
    period, account_id = _period_and_account(tool_context)
    findings = _state(tool_context).get("findings") or compute_findings(period, account_id=account_id)
    cust = [f for f in findings if f["customer_id"] == customer_id]
    if not cust:
        return {"customer_id": customer_id, "memo": "No recoverable findings for this customer."}
    memo, total = build_corrective_memo(customer_id, cust, period)
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


def discover_rights_for_book(tool_context) -> dict:
    """Report AI-discovered candidate financial rights persisted for this
    account (from uploaded contract documents). Novel only — legacy B2B rights
    stay with `run_reconciliation`."""
    _, account_id = _period_and_account(tool_context)
    candidates = db.get_candidate_rights(account_id) if account_id is not None else []
    by_status: dict[str, int] = {}
    for c in candidates:
        by_status[c.get("status", "unknown")] = by_status.get(c.get("status", "unknown"), 0) + 1
    _state(tool_context)["candidates"] = candidates
    return {
        "candidate_count": len(candidates),
        "by_status": by_status,
        "candidates": [
            {"candidate_id": c.get("candidate_id"),
             "right_family": c.get("right_family"),
             "name": c.get("name"),
             "status": c.get("status")}
            for c in candidates],
        "message": ("No novel rights discovered yet; upload contract documents."
                    if not candidates else
                    "These are AI-discovered candidates; only 'compiled' ones can be evaluated."),
    }


def evaluate_compiled_rights(tool_context) -> dict:
    """Evaluate persisted compiled rights against recorded observations.
    Deterministic — no LLM math."""
    period, account_id = _period_and_account(tool_context)
    if account_id is None:
        return {"evaluations": [], "message": "Sample mode has no compiled rights."}
    from .rights_discovery import evaluate_right
    from .rights_discovery.models import RightSpec
    compiled = db.get_compiled_rights(account_id)
    observations = db.get_observations(account_id)
    periods = sorted({o.get("period") for o in observations if o.get("period")}) or [period]
    evaluations = []
    for cr in compiled:
        try:
            spec = RightSpec.from_dict(cr["spec"])
        except Exception:
            continue
        for p in periods:
            evaluations.append(evaluate_right(spec, observations, p).to_dict())
    _state(tool_context)["evaluations"] = evaluations
    recoverable = sum(
        (e.get("expected_amount") or 0) - (e.get("actual_amount") or 0)
        for e in evaluations
        if e.get("status") == "evaluated" and e.get("triggered"))
    return {
        "evaluation_count": len(evaluations),
        "evaluations": evaluations,
        "total_novel_recoverable": round(max(recoverable, 0.0), 2),
    }


def _novel_recovery_context(finding_id: str, account_id: str | None) -> dict | None:
    if account_id is None:
        return None
    finding = next(
        (f for f in db.get_all_findings(account_id)
         if f.get("finding_id") == finding_id
         and str(f.get("type", "")).startswith("novel:")), None)
    if finding is None:
        return None
    spec = next(
        (c for c in db.get_compiled_rights(account_id, finding.get("customer_id"))
         if c.get("spec", {}).get("right_id") == finding.get("right_id")), None)
    meta = (spec or {}).get("metadata") or {}
    return {
        "discrepancy_id": finding.get("discrepancy_id") or finding_id,
        "right_summary": meta.get("description") or finding.get("title"),
        "right_family": finding.get("type", "").split(":", 1)[-1],
        "source_evidence": meta.get("source_quote") or finding.get("clause_text"),
        "observed_facts": {"period": finding.get("period")},
        "governing_authority": finding.get("customer_id"),
        "amount": finding.get("monthly_recoverable"),
        "calculation_trace": {"formula": finding.get("math")},
        "confidence": finding.get("confidence_score"),
    }


def build_recovery_case_tool(finding_id: str, tool_context) -> dict:
    """Build an LLM-investigated recovery case for a novel-right finding. The
    amount and calculation always come from the deterministic evaluation."""
    _, account_id = _period_and_account(tool_context)
    ctx = _novel_recovery_context(finding_id, account_id)
    if ctx is None:
        return {"finding_id": finding_id, "status": "not_found"}
    from .rights_discovery import build_recovery_case
    return build_recovery_case(ctx).to_dict()


def recommend_recovery_tool(finding_id: str, tool_context) -> dict:
    """Recommend a recovery strategy for a novel-right finding. Always requires
    human approval; unknown strategies route to manual_review."""
    _, account_id = _period_and_account(tool_context)
    ctx = _novel_recovery_context(finding_id, account_id)
    if ctx is None:
        return {"finding_id": finding_id, "status": "not_found"}
    from .rights_discovery import build_recovery_case, recommend_recovery
    case = build_recovery_case(ctx).to_dict()
    return recommend_recovery(case).to_dict()
