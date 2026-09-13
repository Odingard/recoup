"""Draft communication text for recovery actions.

Template drafts are deterministic. Model drafts may only rephrase; the exact
authoritative amount must survive and no invented amounts are allowed — any
violation falls back to the template. Model failures never raise."""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel

from ..money import quantize
from ..rights_discovery.discovery import DEFAULT_MODEL, generate

_MONEY_RE = re.compile(r"\$[\d,]+\.\d{2}")

DRAFT_SYSTEM = (
    "You are drafting a short, professional B2B collections/contract-notice "
    "letter. Rules you must obey:\n"
    "- The requested amount is given verbatim; reproduce it exactly once or "
    "more, formatted exactly as provided (e.g. $1,234.56). Never invent, "
    "round, or alter dollar amounts.\n"
    "- The only dollar figures permitted in the output are the amounts "
    "listed in the input under 'allowed amounts'.\n"
    "- Quote the contract clause excerpt if provided; do not fabricate "
    "clauses.\n"
    "- Output JSON: {\"body\": \"...\"}."
)


class ActionDraftOutput(BaseModel):
    body: str


_ACTION_LABELS = {
    "corrective_invoice": "a corrective invoice request",
    "credit_request": "a credit request",
    "rebate_request": "a rebate request",
    "reimbursement_request": "a reimbursement request",
    "contractual_notice": "a formal contractual notice",
    "counterparty_inquiry": "an inquiry about a billing discrepancy",
    "manual_recovery_action": "a recovery follow-up note",
}


def _clause_excerpt(finding: dict, limit: int = 400) -> str:
    text = (finding.get("clause_text") or finding.get("provenance") or "").strip()
    return text[:limit] + ("…" if len(text) > limit else "") if text else ""


def template_draft(finding: dict, action_type: str) -> str:
    """Deterministic per-type letter embedding the authoritative amount."""
    amount = quantize(finding.get("monthly_recoverable") or 0,
                      finding.get("currency") or "USD")
    customer = finding.get("customer_name") or finding.get("customer_id") or "Counterparty"
    period = finding.get("period") or "the billing period covered"
    clause = _clause_excerpt(finding)
    math = finding.get("math") or ""
    label = _ACTION_LABELS.get(action_type, "a recovery request")

    intro = {
        "corrective_invoice": "We are issuing a corrective invoice for amounts "
                              "billed below the contracted terms.",
        "credit_request": "We are requesting a credit for amounts billed below "
                          "the contracted terms.",
        "rebate_request": "We are requesting a rebate under the terms of the "
                          "agreement.",
        "reimbursement_request": "We are requesting reimbursement under the "
                                 "terms of the agreement.",
        "contractual_notice": "This letter constitutes formal notice under the "
                              "agreement of amounts owed.",
        "counterparty_inquiry": "We are writing about a discrepancy between the "
                                "agreement and invoices issued.",
        "manual_recovery_action": "Recovery follow-up for amounts billed below "
                                  "the contracted terms.",
    }.get(action_type, "Recovery request for amounts billed below the contracted terms.")

    letter = (
        f"To the accounts payable team at {customer},\n\n"
        f"During a routine reconciliation of our agreement against invoices "
        f"issued for {period}, we identified amounts billed below the "
        f"contracted terms. This is {label}.\n\n"
        f"{intro}\n\n"
        f"- {period}: {finding.get('title') or finding.get('type') or 'Finding'} — "
        f"${amount:,.2f}" + (f" ({math})" if math else "") + "\n\n"
    )
    if clause:
        letter += (f"The supporting contract clause reads: \"{clause}\"\n\n")
    letter += (
        f"The outstanding amount under the agreement is ${amount:,.2f}. Please "
        f"respond within 15 days if you believe any item is incorrect.\n\n"
        f"Regards,\n[Your company]"
    )
    return letter


def allowed_amounts(finding: dict) -> list[str]:
    """Formatted dollar strings permitted in a draft: requested amount plus
    expected/actual values."""
    currency = finding.get("currency") or "USD"
    out = [f"${quantize(finding.get('monthly_recoverable') or 0, currency):,.2f}"]
    for key in ("expected_value", "actual_value"):
        v = finding.get(key)
        if v is not None:
            out.append(f"${quantize(v, currency):,.2f}")
    return out


def validate_draft_text(finding: dict, text: str) -> str | None:
    """Return an error string if the authoritative amount is missing or an
    unexpected dollar figure appears; None when valid."""
    allowed = set(allowed_amounts(finding))
    authoritative = allowed_amounts(finding)[0]
    if authoritative not in (text or ""):
        return f"draft must contain the authoritative amount {authoritative}"
    for m in _MONEY_RE.findall(text or ""):
        if m not in allowed:
            return f"draft contains unexpected amount {m}"
    return None


def model_draft(finding: dict, action_type: str, *,
                client=None, model: str = DEFAULT_MODEL) -> tuple[str, str]:
    """LLM-suggested wording, post-validated. On any failure or validation
    miss, falls back to (template_draft(...), 'template'). Never raises."""
    try:
        if client is None:
            from ..rights_discovery.discovery import _default_client
            client = _default_client()
        amount = allowed_amounts(finding)[0]
        contents = (
            f"Action type: {action_type}\n"
            f"Counterparty: {finding.get('customer_name') or finding.get('customer_id')}\n"
            f"Period: {finding.get('period')}\n"
            f"Issue: {finding.get('title') or finding.get('type')}\n"
            f"Calculation: {finding.get('math') or ''}\n"
            f"Clause excerpt: {_clause_excerpt(finding)}\n"
            f"Requested amount (use verbatim): {amount}\n"
            f"Allowed amounts: {', '.join(allowed_amounts(finding))}\n"
        )
        out = generate(client, DRAFT_SYSTEM, contents, ActionDraftOutput, model)
        body = (out.body or "").strip()
        if not body or validate_draft_text(finding, body) is not None:
            return template_draft(finding, action_type), "template"
        return body, "model"
    except Exception:
        return template_draft(finding, action_type), "template"
