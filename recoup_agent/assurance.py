"""Continuous Revenue Assurance (phase 1): event-driven scoped re-evaluation.

Every ingest event produces a ChangeEvent; evaluate_event re-runs the
deterministic reconcile() for ONLY the impacted customer's periods and upserts
findings idempotently (db.save_findings preserves status/created_at). No
external action ever happens here — no billing, no Stripe, no recovery
transitions. Deterministic reconciliation remains the only money path.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone

from . import db
from .document_quality import LowConfidenceGateException, NEEDS_VERIFICATION, require_verified_contracts
from .pipeline import compute_findings_and_review

logger = logging.getLogger(__name__)

TRIGGERS = ("new_invoice", "new_billing_period", "new_usage", "new_payment",
            "new_credit_refund", "new_agreement", "agreement_amendment",
            "contract_renewal", "term_expiration", "pricing_change")

_PERIOD_TRIGGERS = {"new_invoice", "new_billing_period", "new_usage",
                    "new_payment", "new_credit_refund"}
_CONTRACT_TRIGGERS = set(TRIGGERS) - _PERIOD_TRIGGERS

# Contract fields whose change reprices the agreement.
_PRICING_FIELDS = ("committed_minimum_monthly", "minimum_schedule", "included_units",
                   "overage_rate", "overage_tiers", "annual_escalator_pct",
                   "escalator_effective_date", "seat_price", "committed_seats",
                   "seats", "discounts")

# Real normalized contract keys for the agreement dates.
_START_KEYS = ("term_start", "start_date", "term_start_date")
_END_KEYS = ("term_end", "end_date", "term_end_date")

_DISCOUNT_MONEY_KEYS = ("label", "name", "type", "value", "applies_to",
                        "starts", "start_date", "expires", "end_date")
_SCHEDULE_MONEY_KEYS = ("amount", "effective_date")
_TIER_MONEY_KEYS = ("up_to", "rate")

# Invoice content that can change the expected-vs-actual calculation. Identifiers,
# provenance, and received/upload timestamps are intentionally excluded.
_INVOICE_MONEY_FIELDS = (
    "base_charge", "overage_charge", "discounts_applied", "amount_billed",
    "amount", "total", "total_billed", "credits_applied", "tax_excluded",
    "prorated", "proration_amount", "units", "quantity", "lines", "line_items",
    "items", "currency",
)


def _canonical(value):
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        items = [_canonical(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(
            item, sort_keys=True, default=str))
    return value


def _invoice_money_payload(invoice: dict) -> dict:
    return {field: invoice.get(field) for field in _INVOICE_MONEY_FIELDS}


def _first_present(record: dict, keys) -> object:
    for key in keys:
        if record.get(key) is not None:
            return record.get(key)
    return None


def _contract_money_payload(contract: dict) -> dict:
    """Money-bearing contract projection: ids, timestamps, provenance and
    ordering never affect event identity or change detection."""
    discounts = [
        {key: discount.get(key) for key in _DISCOUNT_MONEY_KEYS
         if key in discount}
        for discount in contract.get("discounts") or []]
    schedule = [
        {key: item.get(key) for key in _SCHEDULE_MONEY_KEYS if key in item}
        for item in contract.get("minimum_schedule") or []]
    tiers = [
        {key: item.get(key) for key in _TIER_MONEY_KEYS if key in item}
        for item in contract.get("overage_tiers") or []]
    payload = {
        "customer_id": contract.get("customer_id"),
        "committed_minimum_monthly": contract.get("committed_minimum_monthly"),
        "minimum_schedule": schedule,
        "included_units": contract.get("included_units"),
        "overage_rate": contract.get("overage_rate"),
        "overage_tiers": tiers,
        "annual_escalator_pct": contract.get("annual_escalator_pct"),
        "escalator_effective_date": contract.get("escalator_effective_date"),
        "discounts": discounts,
        "term_start": _first_present(contract, _START_KEYS),
        "term_end": _first_present(contract, _END_KEYS),
        "committed_seats": contract.get("committed_seats")
                           or contract.get("seats"),
        "seat_price": contract.get("seat_price"),
        "currency": contract.get("currency"),
        "confirmed_at": contract.get("confirmed_at"),
        "resolved_at": (contract.get("term_resolutions") or {}).get("resolved_at"),
    }
    return _canonical(payload)


@dataclass
class ChangeEvent:
    event_id: str
    trigger: str
    customer_id: str | None
    period: str | None
    source: str
    payload_hash: str
    received_at: str


@dataclass
class NovelEvaluation:
    findings: list[dict] = field(default_factory=list)
    evaluations: list = field(default_factory=list)
    discrepancies: list = field(default_factory=list)
    not_evaluable: list = field(default_factory=list)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def payload_hash(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def make_event(account_id: str | None, trigger: str, customer_id: str | None,
               period: str | None, source: str, payload) -> ChangeEvent:
    if trigger in _CONTRACT_TRIGGERS and isinstance(payload, dict):
        payload = _contract_money_payload(payload)
    ph = payload_hash(payload)
    event_id = hashlib.sha256(json.dumps(
        [account_id or "", trigger, customer_id or "", period or "", ph],
        sort_keys=True).encode()).hexdigest()[:32]
    return ChangeEvent(event_id=event_id, trigger=trigger, customer_id=customer_id,
                       period=period, source=source, payload_hash=ph,
                       received_at=_now())


def _contract_end(contract: dict) -> str | None:
    for k in _END_KEYS:
        if contract.get(k):
            return contract[k]
    return None


def classify_contract_event(existing: dict | None, incoming: dict) -> str:
    if existing is None:
        return "new_agreement"
    new_end = _contract_end(incoming)
    old_end = _contract_end(existing)
    today = date.today().isoformat()
    status = str(incoming.get("status") or "").lower()
    if status == "expired" or (new_end and new_end < today):
        return "term_expiration"
    if new_end and old_end and new_end > old_end:
        return "contract_renewal"
    if new_end and not old_end:
        return "contract_renewal"
    if _contract_money_payload(existing) == _contract_money_payload(incoming):
        return ""
    for f in _PRICING_FIELDS:
        if _canonical(existing.get(f)) != _canonical(incoming.get(f)):
            return "pricing_change"
    return "agreement_amendment"


def classify_invoice_event(existing: dict | None, incoming: dict) -> list[str]:
    """Invoice docs are keyed customer_id_period, so existing is None exactly
    when the period is unseen for that customer. There is no payment field on
    ingested invoices — new_payment is omitted, not invented."""
    triggers: list[str] = []
    if existing is None:
        triggers += ["new_invoice", "new_billing_period"]
    elif payload_hash(_invoice_money_payload(existing)) != payload_hash(
            _invoice_money_payload(incoming)):
        triggers.append("new_invoice")
    if (existing or {}).get("credits_applied") != incoming.get("credits_applied") \
            and incoming.get("credits_applied"):
        if "new_credit_refund" not in triggers:
            triggers.append("new_credit_refund")
    return triggers


def impacted_scope(account_id: str, event: ChangeEvent, contracts: list[dict],
                   usage: list[dict], invoices: list[dict]
                   ) -> tuple[list[str], list[str]] | None:
    """(customer_ids, periods) to re-evaluate, or None when the event's
    customer cannot be resolved to a contract on file."""
    cid = event.customer_id
    if not cid:
        return None
    if cid not in {c.get("customer_id") for c in contracts}:
        return None
    if event.trigger in _CONTRACT_TRIGGERS:
        periods = sorted({r.get("period") for r in list(usage) + list(invoices)
                          if r.get("customer_id") == cid and r.get("period")})
        return [cid], periods
    if event.trigger in _PERIOD_TRIGGERS:
        return [cid], ([event.period] if event.period else [])
    return None


def _math_from_trace(trace: dict) -> str:
    parts = []
    for name, t in (trace.get("trigger") or {}).items():
        if isinstance(t, dict):
            parts.append(
                f"{name} {t.get('op')} {t.get('threshold')} "
                f"(observed {t.get('observed')})")
        else:
            parts.append(f"{name}: {t}")
    calc = trace.get("calculation")
    if isinstance(calc, dict):
        parts.append(
            f"{calc.get('type')}: expected {calc.get('expected')} "
            f"− actual {calc.get('actual')} = {calc.get('recoverable')}")
    elif calc is not None:
        parts.append(str(calc))
    return " | ".join(parts)


def build_novel_findings(account_id: str, customer_id: str, period: str) -> NovelEvaluation:
    """Evaluate persisted compiled rights for one customer/period and project
    discrepancies into finding dicts. finding_id is stable across re-evals
    (sha1 of discrepancy_id); existing findings dedupe by discrepancy_id."""
    from .rights_discovery import evaluate_right
    from .rights_discovery.models import RightSpec
    from .rights_graph.adapter import project_novel_rights

    compiled = [c for c in db.get_compiled_rights(account_id)
                if c.get("customer_id") == customer_id]
    observations = db.get_observations(account_id, customer_id, period)

    evaluations = []
    for cr in compiled:
        try:
            spec = RightSpec.from_dict(cr["spec"])
        except Exception:
            continue
        evaluations.append(evaluate_right(spec, observations, period))

    graph = project_novel_rights(compiled, observations, evaluations, account_id)

    existing_dsc = {f.get("discrepancy_id")
                    for f in db.get_all_findings(account_id)}
    findings = []
    for disc in sorted((d for d in graph.discrepancies
                        if d.discrepancy_id not in existing_dsc),
                       key=lambda d: d.discrepancy_id):
        evaluation = next((e for e in evaluations
                           if e.right_id == disc.right_id), None)
        trace = (evaluation.calculation_trace if evaluation else {}) or {}
        calc = trace.get("calculation") if isinstance(trace.get("calculation"), dict) else {}
        findings.append({
            "finding_id": f"F-{customer_id.upper()}-{period.replace('-', '')}-N-"
                          + hashlib.sha1(disc.discrepancy_id.encode()).hexdigest()[:8].upper(),
            "customer_id": customer_id,
            "customer": customer_id,
            "type": f"novel:{disc.discrepancy_type}",
            "title": f"Novel right: {disc.discrepancy_type}",
            "severity": "needs_review",
            "confidence_score": disc.confidence or 0.0,
            "monthly_recoverable": round(disc.recoverable_amount or 0.0, 2),
            "period": period,
            "math": _math_from_trace(trace),
            "clause_text": next(
                (e.quoted_text for e in graph.evidence
                 if e.evidence_id in (next(
                     (r.evidence_refs for r in graph.rights
                      if r.right_id == disc.right_id), []))),
                None),
            "expected_value": calc.get("expected"),
            "actual_value": calc.get("actual"),
            "status": "open",
            "created_at": _now(),
            "discrepancy_id": disc.discrepancy_id,
            "right_id": disc.right_id,
            "expected_state_id": disc.expected_state_id,
        })
    return NovelEvaluation(findings=findings, evaluations=evaluations,
                           discrepancies=list(graph.discrepancies),
                           not_evaluable=list(graph.not_evaluable))


STALE_WITHDRAWAL_REASON = db.STALE_WITHDRAWAL_REASON


def _withdraw_stale_findings(account_id: str, customer_ids: list[str],
                             periods: list[str], fresh: list[dict],
                             usage_list: list[dict], invoices_list: list[dict]
                             ) -> list[str]:
    """Close still-open rule findings in the evaluated scope that a complete
    re-evaluation did not reproduce. Only 'open' findings for customer/periods
    with both usage and invoice data on file are touched; approved and later
    states, and novel-right findings, are never reset."""
    complete = {(r.get("customer_id"), r.get("period")) for r in usage_list} & \
               {(r.get("customer_id"), r.get("period")) for r in invoices_list}
    scope = {(cid, p) for cid in customer_ids for p in periods} & complete
    if not scope:
        return []
    fresh_ids = {f.get("finding_id") for f in fresh}
    withdrawn: list[str] = []
    for f in db.get_all_findings(account_id):
        if f.get("status") != "open":
            continue
        if (f.get("customer_id"), f.get("period")) not in scope:
            continue
        if f.get("finding_id") in fresh_ids or str(f.get("type") or "").startswith("novel:"):
            continue
        try:
            db.transition_finding_status(
                account_id, f["finding_id"], "rejected",
                "assurance_withdrawn_stale",
                fields={"withdrawal_reason": STALE_WITHDRAWAL_REASON,
                        "withdrawn_by": "system",
                        "withdrawn_at": _now()})
            withdrawn.append(f["finding_id"])
        except Exception:
            logger.warning("could not withdraw stale finding %s",
                           f.get("finding_id"), exc_info=True)
    return withdrawn


def evaluate_event(account_id: str, event: ChangeEvent) -> dict:
    """Scoped re-evaluation for one change event. Never raises to the caller —
    failures are recorded on the event doc so ingest still succeeds."""
    base = {"event_id": event.event_id, "trigger": event.trigger,
            "customer_id": event.customer_id, "period": event.period,
            "source": event.source}
    try:
        if db.assurance_event_exists(account_id, event.event_id):
            return {**base, "status": "duplicate"}
        contracts = db.get_all_contracts(account_id)
        require_verified_contracts(contracts, event.customer_id)
        usage_list = db.get_all_usage(account_id)
        invoices_list = db.get_all_invoices(account_id)
        scope = impacted_scope(account_id, event, contracts, usage_list, invoices_list)
        if scope is None:
            db.save_assurance_event(account_id, {
                **asdict(event), "status": "needs_review",
                "reason": "Could not resolve impacted customer/agreement"})
            _refresh_status(account_id)
            return {**base, "status": "needs_review",
                    "reason": "Could not resolve impacted customer/agreement"}

        customer_ids, periods = scope
        all_findings: list[dict] = []
        all_review: list[dict] = []
        for period in periods:
            findings, review = compute_findings_and_review(
                period, account_id=account_id,
                book=(contracts, usage_list, invoices_list),
                customer_ids=set(customer_ids))
            all_findings.extend(findings)
            all_review.extend(review)
            for cid in customer_ids:
                try:
                    novel = build_novel_findings(account_id, cid, period)
                    all_findings.extend(novel.findings)
                except Exception:
                    logger.warning("novel-rights evaluation failed for %s/%s",
                                   cid, period, exc_info=True)
                    all_review.append({
                        "customer_id": cid, "term": "novel_rights",
                        "reason": "Compiled-rights evaluation failed for this period",
                        "suggested_action": "Re-run the evaluation or inspect the compiled right"})
        if all_findings:
            db.save_findings(account_id, all_findings)
        withdrawn = _withdraw_stale_findings(
            account_id, customer_ids, periods, all_findings,
            usage_list, invoices_list)

        db.save_assurance_event(account_id, {
            **asdict(event), "status": "evaluated",
            "customer_ids": customer_ids, "periods": periods,
            "findings_upserted": len(all_findings),
            "findings_withdrawn": withdrawn,
            "needs_review": all_review, "evaluated_at": _now()})
        db.append_assurance_audit(account_id, {
            "event": "assurance_evaluated", "event_id": event.event_id,
            "trigger": event.trigger, "customer_ids": customer_ids,
            "periods": periods,
            "finding_ids": [f["finding_id"] for f in all_findings]})
        _refresh_status(account_id)
        return {**base, "status": "evaluated", "customer_ids": customer_ids,
                "periods": periods, "findings_upserted": len(all_findings),
                "findings_withdrawn": withdrawn,
                "needs_review_count": len(all_review)}
    except LowConfidenceGateException as exc:
        db.save_assurance_event(account_id, {
            **asdict(event), "status": NEEDS_VERIFICATION,
            "needs_review": [exc.payload()], "evaluated_at": _now()})
        _refresh_status(account_id)
        return {**base, "status": NEEDS_VERIFICATION, "needs_review_count": 1}
    except Exception as exc:
        logger.warning("assurance event %s failed", event.event_id, exc_info=True)
        try:
            db.save_assurance_event(account_id, {
                **asdict(event), "status": "error", "reason": str(exc)})
        except Exception:
            pass
        return {**base, "status": "error", "reason": str(exc)}


def _sources_monitored(account_id: str) -> list[str]:
    sources = []
    if db.get_all_contracts(account_id):
        sources.append("contracts")
    if db.get_all_invoices(account_id):
        sources.append("invoices")
    if db.get_all_usage(account_id):
        sources.append("usage")
    try:
        from .billing.connector_keys import resolve_connector_key
        if resolve_connector_key(account_id):
            sources.append("stripe")
    except Exception:
        pass
    return sources


def _refresh_status(account_id: str) -> dict:
    prev = db.get_assurance_status(account_id) or {}
    events = db.get_assurance_events(account_id, limit=50)
    findings = db.get_all_findings(account_id)
    latest_evaluated = next((e for e in events if e.get("evaluated_at")), None)
    verification_queue = [
        c["structural_verification"] for c in db.get_all_contracts(account_id)
        if c.get("verification_scope") and c.get("verification_state") == NEEDS_VERIFICATION]
    status = {
        "state": NEEDS_VERIFICATION if verification_queue else "Ready",
        "verification_queue": verification_queue,
        "last_evaluated_at": (latest_evaluated or {}).get("evaluated_at"),
        "last_trigger": events[0].get("trigger") if events else None,
        "next_evaluation": "on next event",
        "sources_monitored": _sources_monitored(account_id),
        "open_discrepancies": sum(1 for f in findings if f.get("status") == "open"),
        "needs_review": sum(len(e.get("needs_review") or []) for e in events[:10])
                        + int(prev.get("last_ingest_needs_review") or 0) + len(verification_queue),
        "events_total": len(events),
        "events_needs_review": sum(1 for e in events
                                   if e.get("status") == "needs_review"),
        "recent_events": [
            {"event_id": e.get("event_id"), "trigger": e.get("trigger"),
             "customer_id": e.get("customer_id"), "period": e.get("period"),
             "status": e.get("status"), "received_at": e.get("received_at")}
            for e in events[:10]],
    }
    db.set_assurance_status(account_id, status)
    return status


def account_status(account_id: str) -> dict:
    """Current assurance status; computes fresh and persists."""
    return _refresh_status(account_id)
