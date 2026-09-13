"""Governed recovery actions: Recoup prepares, humans approve and execute.

Nothing external runs unless the action is `approved` and a human issues an
explicit execute call. `requested_value` is copied from the finding's
authoritative `monthly_recoverable` at creation and asserted on every
transition/execute — no API, model, or adapter can change it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from ..money import quantize
from ..rights_graph.ids import stable_id

ACTION_TYPES = [
    "corrective_invoice", "credit_request", "rebate_request",
    "reimbursement_request", "contractual_notice", "counterparty_inquiry",
    "manual_recovery_action",
]

ACTION_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"pending_approval", "rejected"},
    "pending_approval": {"approved", "rejected", "draft"},
    "approved": {"sent", "rejected"},
    "sent": {"awaiting_response", "resolved", "disputed"},
    "awaiting_response": {"resolved", "disputed", "written_off"},
    "disputed": {"resolved", "written_off", "awaiting_response"},
    "resolved": set(), "rejected": set(), "written_off": set(),
}

ACTIONABLE_FINDING_STATUSES = {"approved", "invoiced", "disputed"}


class AmountTampered(Exception):
    """requested_value does not match the finding's authoritative amount."""


class NotAuthorized(Exception):
    """Execution attempted before approval or with a tampered amount."""


def assert_action_transition(current: str, new: str) -> None:
    allowed = ACTION_TRANSITIONS.get(current, set())
    if new not in allowed:
        raise ValueError(
            f"cannot move recovery action from '{current}' to "
            f"'{new}'; allowed: {sorted(allowed) or 'none'}")


@dataclass
class RecoveryAction:
    recovery_action_id: str
    account_id: str | None
    customer_id: str
    customer_name: str
    finding_id: str
    discrepancy_id: str | None
    recovery_case_id: str
    action_type: str
    requested_value: float
    currency: str = "USD"
    evidence_references: list = field(default_factory=list)
    draft_communication: str = ""
    draft_source: str = "template"
    approval_status: str = "not_requested"
    approver: str | None = None
    created_by: str | None = None
    created_at: str = ""
    approved_at: str | None = None
    executed_at: str | None = None
    external_reference: str | None = None
    channel: str | None = None
    status: str = "draft"
    outcome: dict | None = None
    history: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RecoveryAction":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _evidence_references(finding: dict) -> list[dict]:
    refs = []
    if finding.get("clause_ref") or finding.get("clause_text"):
        refs.append({"kind": "clause",
                     "clause_ref": finding.get("clause_ref"),
                     "clause_text": finding.get("clause_text")})
    if finding.get("math"):
        refs.append({"kind": "calculation", "math": finding.get("math")})
    if finding.get("provenance"):
        refs.append({"kind": "provenance", "provenance": finding.get("provenance")})
    if finding.get("period"):
        refs.append({"kind": "period", "period": finding.get("period")})
    return refs


def new_action(account_id: str | None, finding: dict, action_type: str, *,
               created_by: str | None, seq: int = 0,
               draft: str | None = None, draft_source: str = "template",
               now: str | None = None) -> RecoveryAction:
    """Create a draft RecoveryAction. The finding must already be approved,
    invoiced, or disputed; requested_value is the finding's amount."""
    if action_type not in ACTION_TYPES:
        raise ValueError(f"unknown action_type '{action_type}'")
    if (finding.get("status") or "open") not in ACTIONABLE_FINDING_STATUSES:
        raise ValueError(
            "recovery action requires an approved finding "
            f"(status '{finding.get('status', 'open')}')")
    now = now or datetime.now(timezone.utc).isoformat()
    finding_id = finding.get("finding_id") or ""
    action = RecoveryAction(
        recovery_action_id=stable_id("recovery_action", account_id or "sample",
                                     finding_id, action_type, seq),
        account_id=account_id,
        customer_id=finding.get("customer_id") or "",
        customer_name=finding.get("customer_name")
                      or finding.get("customer_id") or "",
        finding_id=finding_id,
        discrepancy_id=finding.get("discrepancy_id"),
        recovery_case_id=finding_id,
        action_type=action_type,
        requested_value=quantize(finding.get("monthly_recoverable") or 0,
                                 finding.get("currency") or "USD"),
        currency=finding.get("currency") or "USD",
        evidence_references=_evidence_references(finding),
        draft_communication=draft or "",
        draft_source=draft_source,
        created_by=created_by,
        created_at=now,
        history=[{"ts": now, "event": "created", "from": None, "to": "draft",
                  "actor": created_by, "details": {"action_type": action_type,
                                                 "draft_source": draft_source}}],
    )
    if action.draft_communication == "":
        from .drafting import template_draft
        action.draft_communication = template_draft(finding, action_type)
    return action


def assert_amount_unchanged(action: RecoveryAction | dict, finding: dict) -> None:
    """Raise AmountTampered if the requested amount no longer equals the
    finding's authoritative monthly_recoverable."""
    value = (action.requested_value if isinstance(action, RecoveryAction)
             else action.get("requested_value"))
    expected = quantize(finding.get("monthly_recoverable") or 0,
                        finding.get("currency") or "USD")
    if quantize(value or 0, finding.get("currency") or "USD") != expected:
        raise AmountTampered(
            f"requested_value {value} != authoritative {expected}")
