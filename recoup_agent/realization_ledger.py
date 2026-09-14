"""Realization ledger: a pure projection of realization events, recovery
actions, and audit entries into per-case ledgers and aggregate recovery
metrics. Realization events remain the single immutable source of realized
money — this module adds nothing that isn't already there; fee numbers are
read out of the existing helpers only."""
from __future__ import annotations

from datetime import datetime, timezone

from .billing import realized_value as rv
from .billing.realized_value import RECOVERY_BASES
from .billing.recoup_billing import SUCCESS_FEE_PCT
from .money import quantize

RESOLUTION_TYPES = RECOVERY_BASES + ["rejected", "disputed", "written_off"]

_ACTION_OPEN = {"draft", "rejected"}


def _dt(ts) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def assert_no_double_count(events: list) -> str:
    """Verify: event ids are unique; per-original reversal sums never exceed
    the original's realized value. Returns 'ok' or 'violation: ...' — never
    raises."""
    seen = set()
    for e in events or []:
        eid = e.get("recovery_event_id")
        if eid in seen:
            return f"violation: duplicate event id {eid}"
        seen.add(eid)
    realized = {e.get("recovery_event_id"): float(e.get("realized_value") or 0)
                for e in events or [] if e.get("event_type") != "reversal"}
    reversed_by: dict[str, float] = {}
    for e in events or []:
        if e.get("event_type") == "reversal":
            orig = e.get("reverses_event_id")
            reversed_by[orig] = reversed_by.get(orig, 0.0) + \
                float(e.get("reversal_amount") or 0)
    for orig, total in reversed_by.items():
        if total > realized.get(orig, 0.0) + 1e-9:
            return f"violation: reversals {total} exceed realization {orig}"
    return "ok"


def case_ledger(finding: dict, events: list, actions: list,
                audit_log=(), *, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    events = [e for e in events or []
              if e.get("finding_id") == finding.get("finding_id")]
    actions = [a for a in actions or []
               if a.get("finding_id") == finding.get("finding_id")]
    audit = [a for a in audit_log or []
             if a.get("finding_id") == finding.get("finding_id")]

    potential_value = float(finding.get("monthly_recoverable") or 0)
    status = finding.get("status") or "open"

    non_draft = [a for a in actions if a.get("status") not in _ACTION_OPEN]
    latest_action = (sorted(non_draft, key=lambda a: a.get("created_at") or "")
                     [-1] if non_draft else None)
    if latest_action is not None:
        approved_requested_value = float(
            latest_action.get("requested_value") or 0)
    else:
        approved_requested_value = (potential_value if status in {
            "approved", "invoiced", "disputed", "recovered"} else 0.0)

    realized_value = quantize(sum(float(e.get("realized_value") or 0)
                                  for e in events
                                  if e.get("event_type") == "realization"))
    reversed_value = quantize(sum(float(e.get("reversal_amount") or 0)
                                  for e in events
                                  if e.get("event_type") == "reversal"))
    net = rv.net_realized(events)

    if status in {"recovered", "written_off", "rejected"}:
        outstanding_value = 0.0
    else:
        outstanding_value = max(approved_requested_value - net, 0.0)
    outstanding_value = quantize(outstanding_value)
    settlement_shortfall = quantize(max(approved_requested_value - net, 0.0)) \
        if status == "recovered" else 0.0

    net_by_basis: dict[str, float] = {}
    for e in events:
        basis = e.get("recovery_basis") or "other"
        delta = (-float(e.get("reversal_amount") or 0)
                 if e.get("event_type") == "reversal"
                 else float(e.get("realized_value") or 0))
        net_by_basis[basis] = net_by_basis.get(basis, 0.0) + delta
    realized_by_basis = {b: quantize(v) for b, v in net_by_basis.items()
                         if v > 0}
    recovery_bases = sorted(realized_by_basis)

    external_references = [{
        "event_id": e.get("recovery_event_id"),
        "basis": e.get("recovery_basis"),
        "reference": e.get("external_reference")
                     or e.get("reversal_reference"),
        "amount": (-float(e.get("reversal_amount") or 0)
                   if e.get("event_type") == "reversal"
                   else float(e.get("realized_value") or 0)),
        "at": e.get("realized_at") or e.get("created_at"),
    } for e in sorted(events, key=lambda x: x.get("created_at") or "")]

    action_dates = [a.get("created_at") for a in actions if a.get("created_at")]
    audit_approved = [a.get("ts") for a in audit
                      if a.get("decision") == "approved" and a.get("ts")]
    first_action_date = (min(action_dates) if action_dates
                         else (min(audit_approved) if audit_approved else None))

    realization_dates = [e.get("realized_at") for e in events
                         if e.get("event_type") == "realization"
                         and e.get("realized_at")]
    realization_date = min(realization_dates) if realization_dates else None
    last_realization_date = max(realization_dates) if realization_dates else None

    created = _dt(finding.get("created_at"))
    first_real = _dt(realization_date)
    days_to_recovery = round((first_real - created).total_seconds() / 86400, 1)\
        if created and first_real else None

    action_statuses = {a.get("status") for a in actions}
    resolved_dispute = any(
        h.get("from") == "disputed" and h.get("to") == "resolved"
        for a in actions for h in a.get("history") or [])
    if status == "disputed" or "disputed" in action_statuses:
        dispute_status = "disputed"
    elif resolved_dispute:
        dispute_status = "resolved_dispute"
    else:
        dispute_status = "none"

    if status == "rejected":
        resolution_status = "rejected"
    elif status == "written_off":
        resolution_status = "written_off"
    elif status == "recovered":
        if net >= approved_requested_value - 0.005:
            resolution_status = "resolved_full"
        elif net > 0:
            resolution_status = "settled"
        else:
            resolution_status = "resolved_unverified"
    elif net > 0:
        resolution_status = "partially_realized"
    elif status == "disputed":
        resolution_status = "disputed"
    else:
        resolution_status = "open"

    chronological = sorted(events, key=lambda e: e.get("created_at") or "")
    event_list = [{
        "recovery_event_id": e.get("recovery_event_id"),
        "event_type": e.get("event_type"),
        "recovery_basis": e.get("recovery_basis"),
        "realized_value": e.get("realized_value"),
        "reversal_amount": e.get("reversal_amount"),
        "realized_at": e.get("realized_at"),
        "external_reference": e.get("external_reference"),
        "reverses_event_id": e.get("reverses_event_id"),
        "recovery_action_id": e.get("recovery_action_id"),
        "lineage": e.get("lineage") or {},
    } for e in chronological]

    return {
        "finding_id": finding.get("finding_id"),
        "potential_value": quantize(potential_value),
        "approved_requested_value": quantize(approved_requested_value),
        "realized_value": realized_value,
        "reversed_value": reversed_value,
        "net_realized": net,
        "outstanding_value": outstanding_value,
        "settlement_shortfall": settlement_shortfall,
        "recovery_bases": recovery_bases,
        "realized_by_basis": realized_by_basis,
        "external_references": external_references,
        "first_action_date": first_action_date,
        "realization_date": realization_date,
        "last_realization_date": last_realization_date,
        "days_to_recovery": days_to_recovery,
        "dispute_status": dispute_status,
        "resolution_status": resolution_status,
        "events": event_list,
        "actions": [
            {"recovery_action_id": a.get("recovery_action_id"),
             "action_type": a.get("action_type"),
             "status": a.get("status"), "channel": a.get("channel"),
             "requested_value": a.get("requested_value"),
             "executed_at": a.get("executed_at")}
            for a in sorted(actions, key=lambda x: x.get("created_at") or "")],
        "fee": {"net_fee": rv.net_fee(events), "fee_pct": SUCCESS_FEE_PCT},
        "integrity": assert_no_double_count(events),
        "generated_at": now.isoformat(),
    }


def recovery_metrics(findings: list, events: list, actions: list,
                     *, now=None) -> dict:
    now = now or datetime.now(timezone.utc)
    non_rejected = [f for f in findings if f.get("status") != "rejected"]
    total_opportunity = quantize(sum(float(f.get("monthly_recoverable") or 0)
                                     for f in non_rejected))

    events_by_finding: dict[str, list] = {}
    for e in events or []:
        events_by_finding.setdefault(e.get("finding_id"), []).append(e)
    actions_by_id = {a.get("recovery_action_id"): a for a in actions or []}

    realized_value = quantize(sum(rv.net_realized(events_by_finding.get(
        f.get("finding_id"), [])) for f in findings))
    recovery_rate = (round(realized_value / total_opportunity, 4)
                     if total_opportunity else 0)

    days = []
    for f in findings:
        dates = [e.get("realized_at") for e in
                 events_by_finding.get(f.get("finding_id"), [])
                 if e.get("event_type") == "realization" and e.get("realized_at")]
        if dates and _dt(f.get("created_at")) and _dt(min(dates)):
            days.append((_dt(min(dates)) - _dt(f["created_at"]))
                        .total_seconds() / 86400)
    avg_time = round(sum(days) / len(days), 1) if days else None

    realized_cases = sum(1 for f in findings
                         if rv.net_realized(events_by_finding.get(
                             f.get("finding_id"), [])) > 0)
    avg_per_case = (round(realized_value / realized_cases, 2)
                    if realized_cases else 0)

    by_type: dict[str, dict] = {}
    for f in non_rejected:
        t = f.get("type") or "unknown"
        slot = by_type.setdefault(t, {"right_type": t, "opportunity": 0.0,
                                      "realized": 0.0, "cases": 0})
        slot["opportunity"] += float(f.get("monthly_recoverable") or 0)
        slot["realized"] += rv.net_realized(
            events_by_finding.get(f.get("finding_id"), []))
        slot["cases"] += 1
    recovery_by_right_type = sorted(
        ({**s, "opportunity": quantize(s["opportunity"]),
          "realized": quantize(s["realized"]),
          "rate": round(s["realized"] / s["opportunity"], 4)
          if s["opportunity"] else 0} for s in by_type.values()),
        key=lambda s: s["realized"], reverse=True)

    by_cust: dict[str, dict] = {}
    for f in non_rejected:
        c = f.get("customer_id") or "unknown"
        slot = by_cust.setdefault(
            c, {"customer_id": c,
                "customer_name": f.get("customer_name") or c,
                "opportunity": 0.0, "realized": 0.0, "cases": 0})
        slot["opportunity"] += float(f.get("monthly_recoverable") or 0)
        slot["realized"] += rv.net_realized(
            events_by_finding.get(f.get("finding_id"), []))
        slot["cases"] += 1
    recovery_by_customer = sorted(
        ({**s, "opportunity": quantize(s["opportunity"]),
          "realized": quantize(s["realized"]),
          "rate": round(s["realized"] / s["opportunity"], 4)
          if s["opportunity"] else 0} for s in by_cust.values()),
        key=lambda s: s["realized"], reverse=True)

    by_strategy: dict[str, dict] = {}
    action_count: dict[str, int] = {}
    resolved_by_strategy: dict[str, set] = {}
    for a in actions or []:
        t = a.get("action_type") or "unattributed"
        action_count[t] = action_count.get(t, 0) + 1
        if a.get("status") == "resolved" or \
                (a.get("outcome") or {}).get("result") == "resolved":
            resolved_by_strategy.setdefault(t, set()).add(a.get("finding_id"))
    for e in events or []:
        delta = (-float(e.get("reversal_amount") or 0)
                 if e.get("event_type") == "reversal"
                 else float(e.get("realized_value") or 0))
        link = actions_by_id.get(e.get("recovery_action_id"))
        t = link.get("action_type") if link else "unattributed"
        slot = by_strategy.setdefault(
            t, {"action_type": t, "actions": action_count.get(t, 0),
                "realized": 0.0, "resolved_cases": 0})
        slot["realized"] += delta
    for t, count in action_count.items():
        by_strategy.setdefault(
            t, {"action_type": t, "actions": count,
                "realized": 0.0, "resolved_cases": 0})
    for t, slot in by_strategy.items():
        slot["actions"] = action_count.get(t, 0)
        slot["realized"] = quantize(slot["realized"])
        slot["resolved_cases"] = len(resolved_by_strategy.get(t, set()))
    recovery_by_action_strategy = sorted(
        by_strategy.values(), key=lambda s: s["realized"], reverse=True)

    resolution_mix: dict[str, int] = {}
    for f in findings:
        ledger_status = _resolution_status_for(f, events_by_finding.get(
            f.get("finding_id"), []), actions)
        resolution_mix[ledger_status] = resolution_mix.get(ledger_status, 0) + 1

    return {
        "total_opportunity": total_opportunity,
        "realized_value": realized_value,
        "recovery_rate": recovery_rate,
        "avg_time_to_recovery": avg_time,
        "avg_recovery_per_case": avg_per_case,
        "recovery_by_right_type": recovery_by_right_type,
        "recovery_by_customer": recovery_by_customer,
        "recovery_by_action_strategy": recovery_by_action_strategy,
        "resolution_mix": resolution_mix,
        "generated_at": now.isoformat(),
    }


def _resolution_status_for(finding: dict, events: list, actions: list) -> str:
    status = finding.get("status") or "open"
    net = rv.net_realized(events)
    potential = float(finding.get("monthly_recoverable") or 0)
    requested = potential
    non_draft = [a for a in actions or []
                 if a.get("finding_id") == finding.get("finding_id")
                 and a.get("status") not in _ACTION_OPEN]
    if non_draft:
        requested = float(sorted(non_draft, key=lambda a: a.get("created_at")
                                 or "")[-1].get("requested_value") or 0)
    if status == "rejected":
        return "rejected"
    if status == "written_off":
        return "written_off"
    if status == "recovered":
        if net >= requested - 0.005:
            return "resolved_full"
        if net > 0:
            return "settled"
        return "resolved_unverified"
    if net > 0:
        return "partially_realized"
    if status == "disputed":
        return "disputed"
    return "open"


def outcome_record(finding: dict, ledger: dict, actions: list) -> dict:
    """Compact, structured outcome record — no free text, no clause text."""
    f_actions = [a for a in actions or []
                 if a.get("finding_id") == finding.get("finding_id")]
    return {
        "finding_id": finding.get("finding_id"),
        "customer_id": finding.get("customer_id"),
        "right_type": finding.get("right_id") or finding.get("type"),
        "discrepancy_id": finding.get("discrepancy_id")
                          or finding.get("finding_id"),
        "potential_value": ledger.get("potential_value"),
        "approved_requested_value": ledger.get("approved_requested_value"),
        "net_realized": ledger.get("net_realized"),
        "resolution_status": ledger.get("resolution_status"),
        "dispute_status": ledger.get("dispute_status"),
        "recovery_bases": ledger.get("recovery_bases"),
        "action_types": sorted({a.get("action_type") for a in f_actions
                                if a.get("action_type")}),
        "executed_channels": sorted({a.get("channel") for a in f_actions
                                     if a.get("channel")}),
        "days_to_recovery": ledger.get("days_to_recovery"),
        "first_action_date": ledger.get("first_action_date"),
        "realization_date": ledger.get("realization_date"),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
    }
