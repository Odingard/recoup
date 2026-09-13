"""Execution channels. Adapters PREPARE artifacts; nothing sends externally.
Every `execute` requires status == 'approved' and an untampered amount.
Registering a new adapter is the only step needed to add a channel."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Protocol

from ..report import _LEAK_LABELS
from .models import NotAuthorized, assert_amount_unchanged


def _gate(action: dict, finding: dict) -> None:
    if action.get("status") != "approved":
        raise NotAuthorized(
            f"cannot execute action in status '{action.get('status')}'")
    assert_amount_unchanged(action, finding)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionAdapter(Protocol):
    name: str

    def prepare(self, action: dict, finding: dict) -> dict: ...

    def execute(self, action: dict, finding: dict, *, actor: str,
                external_reference: str | None = None) -> dict: ...


def _result(channel: str, external_reference: str, artifact: dict) -> dict:
    return {"channel": channel, "external_reference": external_reference,
            "executed_at": _now(), "artifact": artifact}


class DocumentAdapter:
    """Renders the letter + evidence schedule as a PDF for the human to send.
    external_reference = sha256 of the rendered bytes."""

    name = "document"

    def _render(self, action: dict, finding: dict) -> bytes:
        from ..trueup import render_trueup_pdf
        amount = float(action.get("requested_value") or 0)
        clause_ref = next((r.get("clause_ref") for r in
                           action.get("evidence_references", [])
                           if r.get("kind") == "clause"), None)
        clause_text = next((r.get("clause_text") for r in
                            action.get("evidence_references", [])
                            if r.get("kind") == "clause"), "")
        math = next((r.get("math") for r in action.get("evidence_references", [])
                     if r.get("kind") == "calculation"), "")
        pack = {
            "customer_id": finding.get("customer_id"),
            "customer_name": action.get("customer_name")
                             or finding.get("customer_name"),
            "sender": None,
            "total": amount,
            "letter": action.get("draft_communication") or "",
            "periods": [finding.get("period")] if finding.get("period") else [],
            "rows": [{
                "period": finding.get("period") or "",
                "leak_type": _LEAK_LABELS.get(finding.get("type"),
                                             finding.get("type", "")),
                "amount": amount,
                "clause_text": clause_text,
                "math": math,
                "term": clause_ref or "",
                "status": finding.get("status", "approved"),
                "invoice_ref": None,
            }],
        }
        return render_trueup_pdf(pack)

    def prepare(self, action: dict, finding: dict) -> dict:
        pdf = self._render(action, finding)
        import base64
        return {"kind": "document", "content_type": "application/pdf",
                "pdf_base64": base64.b64encode(pdf).decode(),
                "sha256": hashlib.sha256(pdf).hexdigest()}

    def execute(self, action: dict, finding: dict, *, actor: str,
                external_reference: str | None = None) -> dict:
        _gate(action, finding)
        pdf = self._render(action, finding)
        return _result(self.name, hashlib.sha256(pdf).hexdigest(),
                       {"kind": "document", "content_type": "application/pdf",
                        "sha256": hashlib.sha256(pdf).hexdigest()})


class EmailDraftAdapter:
    """Produces a subject/body draft. Never sends — external_reference is a
    deterministic draft id."""

    name = "email_draft"

    def _subject_body(self, action: dict, finding: dict) -> dict:
        subject = (f"{finding.get('title') or 'Contract reconciliation'} — "
                   f"{action.get('customer_name') or finding.get('customer_id')}")
        return {"kind": "email_draft", "to": action.get("customer_name") or "",
                "subject": subject,
                "body": action.get("draft_communication") or ""}

    def prepare(self, action: dict, finding: dict) -> dict:
        return self._subject_body(action, finding)

    def execute(self, action: dict, finding: dict, *, actor: str,
                external_reference: str | None = None) -> dict:
        _gate(action, finding)
        from ..rights_graph.ids import stable_id
        draft_id = stable_id("email_draft", action.get("recovery_action_id"),
                             hashlib.sha256(
                                 (action.get("draft_communication") or "")
                                 .encode()).hexdigest()[:12])
        return _result(self.name, draft_id, self._subject_body(action, finding))


class ManualAdapter:
    """The human performed the action outside the system and records the
    reference. `external_reference` is required."""

    name = "manual"

    def prepare(self, action: dict, finding: dict) -> dict:
        return {"kind": "manual", "content": action.get("draft_communication")
                or "", "requires_external_reference": True}

    def execute(self, action: dict, finding: dict, *, actor: str,
                external_reference: str | None = None) -> dict:
        _gate(action, finding)
        if not external_reference:
            raise NotAuthorized(
                "manual execution requires an external_reference")
        return _result(self.name, external_reference,
                       {"kind": "manual",
                        "content": action.get("draft_communication") or ""})


REGISTRY: dict[str, ExecutionAdapter] = {
    a.name: a for a in (DocumentAdapter(), EmailDraftAdapter(), ManualAdapter())
}
