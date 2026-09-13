"""Recovery Command Center: a pure, deterministic projection of stored
findings, recovery events, audit entries, and contracts into ranked cases,
pipeline metrics, and an executive summary. Computes nothing that isn't
already computed by reconciliation/realized-value — it only aggregates."""
from __future__ import annotations

from datetime import datetime, timezone

from .reconciliation import CONFIDENCE_THRESHOLD
from .success_fee import recovered_dollars

VERIFIED_CONFIDENCE = CONFIDENCE_THRESHOLD  # 0.85

_STATUS_WEIGHT = {
    "open": 1.0, "approved": 0.9, "invoiced": 0.8, "disputed": 0.7,
    "recovered": 0.2, "rejected": 0.0, "written_off": 0.0,
}


def _dt(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def _days_between(later: datetime | None, earlier: datetime | None) -> float | None:
    if later is None or earlier is None:
        return None
    return round((later - earlier).total_seconds() / 86400.0, 1)


def evidence_completeness(f: dict) -> float:
    """Fraction of evidence fields present: clause_text, math, clause_ref,
    provenance, right/type identity, expected_value. 0..1, 2dp."""
    present = sum([
        bool(f.get("clause_text")),
        bool(f.get("math")),
        bool(f.get("clause_ref")),
        bool(f.get("provenance")),
        bool(f.get("right_id") or f.get("type")),
        f.get("expected_value") is not None,
    ])
    return round(present / 6.0, 2)


def is_verified(f: dict) -> bool:
    return (float(f.get("confidence_score") or 0) >= VERIFIED_CONFIDENCE
            and bool(f.get("clause_text")) and bool(f.get("math")))


def next_step(f: dict, realization_events: list[dict] | None,
              actions: list[dict] | None = None) -> str:
    status = f.get("status") or "open"
    if status == "open":
        return "Approve for recovery" if is_verified(f) else "Review evidence / confirm term"
    if status in {"approved", "invoiced", "disputed"}:
        open_statuses = {a.get("status") for a in (actions or [])
                         if a.get("status") in
                         {"draft", "pending_approval", "approved", "sent",
                          "awaiting_response"}}
        if open_statuses & {"sent", "awaiting_response"}:
            return "Await counterparty response"
        if "approved" in open_statuses:
            return "Execute approved action"
        if "pending_approval" in open_statuses:
            return "Approve recovery action"
        if "draft" in open_statuses:
            return "Submit for approval"
        if status == "approved":
            return "Prepare recovery action"
        if status == "invoiced":
            return "Follow up on payment"
        return "Resolve dispute"
    if status == "recovered":
        return ("Closed — verified realized" if realization_events
                else "Record realized value")
    return "None"  # rejected / written_off


def rank_score(f: dict, now: datetime, evidence: float, status_weight: float) -> dict:
    created = _dt(f.get("created_at"))
    age_days = round(max((now - created).total_seconds() / 86400.0, 0.0), 1) \
        if created else 0.0
    value = float(f.get("monthly_recoverable") or 0)
    confidence = float(f.get("confidence_score") or 0)
    value_norm = min(value / 10_000.0, 1.0)
    score = round(0.4 * value_norm + 0.25 * confidence + 0.15 * evidence
                  + 0.1 * min(age_days / 90.0, 1.0) + 0.1 * status_weight, 4)
    return {"score": score, "value": value, "confidence": confidence,
            "age_days": age_days, "evidence": evidence,
            "status_weight": status_weight}


def _approval_state(status: str) -> str:
    if status == "open":
        return "pending"
    if status == "rejected":
        return "rejected"
    return "approved"


def build_command_center(findings: list[dict], recovery_events: list[dict],
                         audit_log: list[dict], contracts: list[dict],
                         *, now: datetime | None = None,
                         assurance_status: dict | None = None,
                         recovery_actions: list[dict] | None = None) -> dict:
    now = now or datetime.now(timezone.utc)

    events_by_finding: dict[str, list[dict]] = {}
    for e in recovery_events or []:
        events_by_finding.setdefault(e.get("finding_id"), []).append(e)
    actions_by_finding: dict[str, list[dict]] = {}
    for a in recovery_actions or []:
        actions_by_finding.setdefault(a.get("finding_id"), []).append(a)
    audit_by_finding: dict[str, list[dict]] = {}
    for a in audit_log or []:
        audit_by_finding.setdefault(a.get("finding_id"), []).append(a)
    contracts_by_cid = {c.get("customer_id"): c for c in contracts or []}

    amount = lambda f: float(f.get("monthly_recoverable") or 0)
    open_f = [f for f in findings if (f.get("status") or "open") == "open"]
    approved_f = [f for f in findings if f.get("status") == "approved"]
    invoiced_f = [f for f in findings if f.get("status") == "invoiced"]
    disputed_f = [f for f in findings if f.get("status") == "disputed"]
    recovered_f = [f for f in findings if f.get("status") == "recovered"]
    written_f = [f for f in findings if f.get("status") == "written_off"]

    verified_open = [f for f in open_f if is_verified(f)]
    realized_value = round(sum(recovered_dollars(f, events_by_finding)
                               for f in recovered_f), 2)

    metrics = {
        "potential_recoverable_value": round(sum(amount(f) for f in open_f), 2),
        "verified_value": round(sum(amount(f) for f in verified_open), 2),
        "needs_review": round(sum(amount(f) for f in open_f
                                  if not is_verified(f)), 2),
        "approved": round(sum(amount(f) for f in approved_f), 2),
        "in_recovery": round(sum(amount(f) for f in invoiced_f + disputed_f), 2),
        "disputed": round(sum(amount(f) for f in disputed_f), 2),
        "realized_value": realized_value,
        "written_off": round(sum(amount(f) for f in written_f), 2),
    }

    pipeline = [
        {"stage": "Potential", "value": metrics["potential_recoverable_value"],
         "count": len(open_f)},
        {"stage": "Verified", "value": metrics["verified_value"],
         "count": len(verified_open)},
        {"stage": "Approved", "value": metrics["approved"],
         "count": len(approved_f)},
        {"stage": "In Recovery", "value": metrics["in_recovery"],
         "count": len(invoiced_f) + len(disputed_f)},
        {"stage": "Realized", "value": realized_value,
         "count": len(recovered_f)},
    ]

    non_rejected = [f for f in findings if f.get("status") != "rejected"]
    total_opportunity = round(sum(amount(f) for f in non_rejected), 2)
    days = [d for f in recovered_f
            if (d := _days_between(_dt(f.get("recovered_at")),
                                   _dt(f.get("created_at")))) is not None]
    by_type: dict[str, dict] = {}
    for f in non_rejected:
        t = f.get("type") or "unknown"
        slot = by_type.setdefault(t, {"type": t, "value": 0.0, "count": 0})
        slot["value"] += amount(f)
        slot["count"] += 1
    top_sources = sorted(by_type.values(), key=lambda s: s["value"], reverse=True)[:5]
    for s in top_sources:
        s["value"] = round(s["value"], 2)

    executive_summary = {
        "total_opportunity": total_opportunity,
        "realized_value": realized_value,
        "recovery_rate": round(realized_value / total_opportunity, 4)
                         if total_opportunity else 0,
        "avg_days_to_recovery": round(sum(days) / len(days), 1) if days else None,
        "open_cases": sum(1 for f in findings
                          if (f.get("status") or "open")
                          not in {"recovered", "rejected", "written_off"}),
        "top_recovery_sources": top_sources,
    }

    cases = []
    for f in findings:
        cid = f.get("customer_id")
        contract = contracts_by_cid.get(cid) or {}
        ev = evidence_completeness(f)
        sw = _STATUS_WEIGHT.get(f.get("status") or "open", 1.0)
        rank = rank_score(f, now, ev, sw)
        f_events = events_by_finding.get(f.get("finding_id"), [])
        f_actions = actions_by_finding.get(f.get("finding_id"), [])
        f_audit = sorted(audit_by_finding.get(f.get("finding_id"), []),
                         key=lambda a: a.get("ts") or "")
        approval = {"state": _approval_state(f.get("status") or "open")}
        approved_entry = next((a for a in f_audit
                               if a.get("decision") == "approved"), None)
        if approved_entry:
            approval["approved_at"] = approved_entry.get("ts")
        cases.append({
            "finding_id": f.get("finding_id"),
            "counterparty": {"customer_id": cid,
                             "customer_name": f.get("customer_name") or cid},
            "agreement": {
                "customer_id": cid,
                "customer_name": contract.get("customer_name")
                                 or f.get("customer_name") or cid,
                "term_start": contract.get("term_start"),
                "term_end": contract.get("term_end"),
                "effective_date": contract.get("effective_date"),
            },
            "financial_right": {"type": f.get("type"),
                                "right_id": f.get("right_id"),
                                "clause_ref": f.get("clause_ref"),
                                "title": f.get("title")},
            "evidence": {"clause_text": f.get("clause_text"),
                         "provenance": f.get("provenance"),
                         "math": f.get("math"),
                         "completeness": ev},
            "period": f.get("period"),
            "expected_value": f.get("expected_value"),
            "actual_value": f.get("actual_value"),
            "recoverable_difference": f.get("monthly_recoverable"),
            "currency": f.get("currency") or "USD",
            "confidence": f.get("confidence_score"),
            "status": f.get("status") or "open",
            "verified": is_verified(f),
            "recommended_next_step": next_step(f, f_events, f_actions),
            "recovery_actions": [
                {"id": a.get("recovery_action_id"),
                 "action_type": a.get("action_type"),
                 "status": a.get("status"),
                 "requested_value": a.get("requested_value"),
                 "channel": a.get("channel"),
                 "executed_at": a.get("executed_at")}
                for a in sorted(f_actions,
                                key=lambda x: x.get("created_at") or "")],
            "approval": approval,
            "recovery_history": [
                {"ts": a.get("ts"), "event": a.get("event"),
                 "decision": a.get("decision"), "details": a.get("details")}
                for a in f_audit],
            "realization_history": f_events,
            "net_realized": recovered_dollars(f, events_by_finding) if f_events else 0.0,
            "rank": rank,
            "age_days": rank["age_days"],
            "created_at": f.get("created_at"),
        })
    cases.sort(key=lambda c: c["rank"]["score"], reverse=True)

    customers = sorted(
        {(f.get("customer_id"), f.get("customer_name") or f.get("customer_id"))
         for f in findings})
    out = {
        "metrics": metrics,
        "pipeline": pipeline,
        "cases": cases,
        "executive_summary": executive_summary,
        "filters": {
            "customers": [{"id": cid, "name": name} for cid, name in customers],
            "statuses": sorted({(f.get("status") or "open") for f in findings}),
            "types": sorted({f.get("type") or "unknown" for f in findings}),
            "agreements": sorted({c.get("customer_id") for c in contracts or []
                                  if c.get("customer_id")}),
            "periods": sorted({f.get("period") for f in findings if f.get("period")}),
        },
        "generated_at": now.isoformat(),
    }
    if assurance_status is not None:
        out["assurance"] = assurance_status
    return out
