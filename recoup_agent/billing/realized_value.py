"""Realized recovered value: the billable event model.

Recoup bills only on value the customer actually realized — cash received,
credits posted, offsets applied. Events are immutable; reversals are new
events, never edits. All fee arithmetic lives here and only here: callers
cannot set fee fields. No LLM, no Stripe calls — deterministic only.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import Any

from ..rights_graph.ids import stable_id
from ..money import quantize
from .recoup_billing import SUCCESS_FEE_PCT

RECOVERY_BASES = [
    "cash_payment", "settlement", "refund", "rebate", "reimbursement",
    "contractual_credit", "offset", "other_verified_value",
]

BILLABLE_FINDING_STATUSES = {"approved", "invoiced", "disputed", "recovered"}
_PAID_STATUSES = {"paid", "pending"}


def _to_value(v: Any) -> Any:
    if isinstance(v, list):
        return [_to_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _to_value(x) for k, x in v.items()}
    return v


@dataclass
class RecoveryRealizationEvent:
    """Immutable record of realized (or reversed) recovered value."""
    recovery_event_id: str
    account_id: str | None
    finding_id: str
    event_type: str  # "realization" | "reversal"
    recovery_basis: str
    realized_value: float
    discrepancy_id: str | None = None
    currency: str = "USD"
    realized_at: str | None = None
    external_reference: str | None = None
    evidence: dict = field(default_factory=dict)
    feeable_value: float = 0.0
    fee_percentage: float = SUCCESS_FEE_PCT
    fee_amount: float = 0.0
    fee_status: str = "unbilled"  # unbilled|pending|paid|error|needs_config|
                                  # not_eligible|adjustment_pending|adjusted
    fee_charge: dict | None = None
    reversal_amount: float = 0.0
    reversal_reference: str | None = None
    reverses_event_id: str | None = None
    recovery_action_id: str | None = None
    lineage: dict = field(default_factory=dict)
    created_at: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {f.name: _to_value(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: dict):
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_realization(account_id, finding: dict, *, recovery_basis: str,
                    realized_value: float, currency: str = "USD",
                    realized_at: str | None = None,
                    external_reference: str | None = None,
                    evidence: dict | None = None,
                    discrepancy_id: str | None = None,
                    recovery_action_id: str | None = None) -> RecoveryRealizationEvent:
    """Create a realization event. Fee fields are computed here — callers
    cannot pass them."""
    if recovery_basis not in RECOVERY_BASES:
        raise ValueError(f"invalid recovery_basis '{recovery_basis}'")
    value = float(realized_value or 0)
    if value <= 0:
        raise ValueError("realized_value must be greater than zero")
    realized_at = realized_at or _now()
    event_id = stable_id(
        "rev", account_id, finding["finding_id"], "realization",
        recovery_basis,
        external_reference or f"{realized_at}:{value}")
    lineage = {
        "agreement": finding.get("customer_id"),
        "financial_right": finding.get("right_id") or finding.get("type"),
        "discrepancy": discrepancy_id or finding.get("discrepancy_id")
                       or finding.get("finding_id"),
        "recovery_case": finding["finding_id"],
        "recovery_action": recovery_action_id,
        "realization_event": event_id,
    }
    return RecoveryRealizationEvent(
        recovery_event_id=event_id,
        account_id=account_id,
        finding_id=finding["finding_id"],
        discrepancy_id=discrepancy_id or finding.get("discrepancy_id"),
        event_type="realization",
        recovery_basis=recovery_basis,
        realized_value=quantize(value),
        currency=currency or "USD",
        realized_at=realized_at,
        external_reference=external_reference,
        evidence=evidence or {},
        feeable_value=quantize(value),
        fee_percentage=SUCCESS_FEE_PCT,
        fee_amount=quantize(value * SUCCESS_FEE_PCT),
        fee_status="unbilled",
        reversal_amount=0.0,
        recovery_action_id=recovery_action_id,
        lineage=lineage,
        created_at=_now(),
    )


def new_reversal(account_id, original: RecoveryRealizationEvent, *,
                 reversal_amount: float, reversal_reference: str | None,
                 reason: str | None,
                 reversed_at: str | None = None,
                 existing_events: list | None = None) -> RecoveryRealizationEvent:
    """Create a reversal against a realization. The original is never mutated."""
    amount = float(reversal_amount or 0)
    if amount <= 0:
        raise ValueError("reversal_amount must be greater than zero")
    already_reversed = sum(
        (e.reversal_amount or 0)
        for e in (existing_events or [])
        if getattr(e, "reverses_event_id", None) == original.recovery_event_id)
    if amount > quantize(original.realized_value - already_reversed) + 1e-9:
        raise ValueError("reversal_amount exceeds remaining realized value")
    reversed_at = reversed_at or _now()
    event_id = stable_id(
        "rev", account_id, original.finding_id, "reversal",
        original.recovery_basis,
        reversal_reference or f"{reversed_at}:{amount}")
    fee_status = ("adjustment_pending"
                  if original.fee_status in _PAID_STATUSES else "not_eligible")
    return RecoveryRealizationEvent(
        recovery_event_id=event_id,
        account_id=account_id,
        finding_id=original.finding_id,
        discrepancy_id=original.discrepancy_id,
        event_type="reversal",
        recovery_basis=original.recovery_basis,
        realized_value=0.0,
        currency=original.currency,
        realized_at=reversed_at,
        external_reference=reversal_reference,
        evidence={"reason": reason} if reason else {},
        feeable_value=-quantize(amount),
        fee_percentage=SUCCESS_FEE_PCT,
        fee_amount=-quantize(amount * SUCCESS_FEE_PCT),
        fee_status=fee_status,
        reversal_amount=quantize(amount),
        reversal_reference=reversal_reference,
        reverses_event_id=original.recovery_event_id,
        recovery_action_id=original.recovery_action_id,
        lineage={**(original.lineage or {}), "realization_event": event_id},
        created_at=_now(),
    )


def billing_eligibility(event: RecoveryRealizationEvent, finding: dict,
                        existing_events: list) -> tuple[bool, str]:
    """Deterministic fee eligibility — no model judgment involved."""
    if event.event_type != "realization":
        return False, "not_a_realization"
    if (event.realized_value or 0) <= 0:
        return False, "zero_value"
    if finding.get("status") not in BILLABLE_FINDING_STATUSES:
        return False, "finding_not_approved"
    if event.fee_status in _PAID_STATUSES:
        return False, "already_billed"
    for existing in existing_events or []:
        eid = existing.get("recovery_event_id") if isinstance(existing, dict) \
            else existing.recovery_event_id
        estatus = existing.get("fee_status") if isinstance(existing, dict) \
            else existing.fee_status
        if eid == event.recovery_event_id and estatus in _PAID_STATUSES:
            return False, "duplicate_event"
    return True, "eligible"


def net_realized(events: list) -> float:
    total = 0.0
    for e in events or []:
        get = e.get if isinstance(e, dict) else lambda k, d=None: getattr(e, k, d)
        if get("event_type") == "reversal":
            total -= get("reversal_amount") or 0
        else:
            total += get("realized_value") or 0
    return quantize(total)


def net_fee(events: list) -> float:
    total = 0.0
    for e in events or []:
        get = e.get if isinstance(e, dict) else lambda k, d=None: getattr(e, k, d)
        total += get("fee_amount") or 0
    return quantize(total)
