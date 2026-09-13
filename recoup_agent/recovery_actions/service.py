"""Pure state machine over recovery-action dicts. No db or network calls —
persistence and audit live in api/db."""
from __future__ import annotations

from datetime import datetime, timezone

from .models import (ACTION_TRANSITIONS, assert_action_transition,
                     assert_amount_unchanged)

OUTCOME_RESULTS = {"resolved", "disputed", "rejected", "written_off"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_history(action: dict, event: str, actor: str | None,
                    details: dict | None, frm: str | None, to: str) -> None:
    action.setdefault("history", []).append(
        {"ts": _now(), "event": event, "from": frm, "to": to,
         "actor": actor, "details": details or {}})


def transition(action: dict, new_status: str, *, actor: str | None,
               details: dict | None = None,
               finding: dict | None = None) -> dict:
    """Assert legality + amount integrity, apply status and the approval
    bookkeeping each edge implies, append history. Mutates and returns action."""
    if finding is not None:
        assert_amount_unchanged(action, finding)
    current = action.get("status", "draft")
    assert_action_transition(current, new_status)
    action["status"] = new_status
    if new_status == "pending_approval":
        action["approval_status"] = "pending"
    elif new_status == "approved":
        action["approval_status"] = "approved"
        action["approver"] = actor
        action["approved_at"] = _now()
    elif new_status == "rejected":
        action["approval_status"] = "rejected"
    elif new_status == "sent":
        action["executed_at"] = _now()
    elif new_status == "draft":
        action["approval_status"] = "not_requested"
    _append_history(action, "transition", actor, details, current, new_status)
    return action


def submit_for_approval(action: dict, *, actor: str | None,
                        details: dict | None = None,
                        finding: dict | None = None) -> dict:
    return transition(action, "pending_approval", actor=actor,
                      details=details, finding=finding)


def approve(action: dict, *, actor: str | None, details: dict | None = None,
            finding: dict | None = None) -> dict:
    return transition(action, "approved", actor=actor, details=details,
                      finding=finding)


def reject(action: dict, *, actor: str | None, details: dict | None = None,
           finding: dict | None = None) -> dict:
    return transition(action, "rejected", actor=actor, details=details,
                      finding=finding)


def execute(action: dict, finding: dict, adapter, actor: str,
            external_reference: str | None = None) -> tuple[dict, dict]:
    """Run the adapter (its gate enforces approved + amount) then move
    approved -> sent, stamping channel/external_reference/executed_at from the
    adapter result. The human moves it onward; we never auto-advance."""
    result = adapter.execute(action, finding, actor=actor,
                             external_reference=external_reference)
    transition(action, "sent", actor=actor,
               details={"channel": result["channel"],
                        "external_reference": result["external_reference"]},
               finding=finding)
    action["channel"] = result["channel"]
    action["external_reference"] = result["external_reference"]
    action["executed_at"] = result["executed_at"]
    return action, result


def record_outcome(action: dict, result: str, note: str | None,
                   response_reference: str | None, actor: str | None) -> dict:
    """Move to resolved/disputed/written_off (or rejected — reachable only via
    its own transition edge) and set `outcome`."""
    if result not in OUTCOME_RESULTS:
        raise ValueError(f"unknown outcome result '{result}'")
    transition(action, result, actor=actor,
               details={"note": note,
                        "response_reference": response_reference})
    action["outcome"] = {
        "result": result, "note": note,
        "response_reference": response_reference,
        "recorded_by": actor, "ts": _now(),
    }
    return action


LEGAL = ACTION_TRANSITIONS
