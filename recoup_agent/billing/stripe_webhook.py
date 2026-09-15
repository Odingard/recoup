from __future__ import annotations

import os
from datetime import datetime, timezone

from .. import db


def _obj(event):
    data = event.get("data") if isinstance(event, dict) else getattr(event, "data", None)
    return data.get("object") if isinstance(data, dict) else getattr(data, "object", {})


def _value(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _event_type(event):
    return event.get("type") if isinstance(event, dict) else getattr(event, "type", "")


def _event_id(event):
    return event.get("id") if isinstance(event, dict) else getattr(event, "id", "")


def _audit(account_id: str | None, event, extra: dict | None = None):
    if account_id:
        db.append_assurance_audit(account_id, {
            "event": "stripe_webhook", "type": _event_type(event),
            "stripe_event_id": _event_id(event), **(extra or {})})


def handle_event(event) -> dict:
    event_type = _event_type(event)
    obj = _obj(event)
    account_id = None
    recovery = None
    invoice_id = _value(obj, "id") if event_type.startswith("invoice.") else _value(obj, "invoice")
    if invoice_id:
        match = db.find_recovery_event_by_fee_invoice(invoice_id)
        if match:
            account_id, recovery = match

    if event_type in {"invoice.paid", "invoice.payment_succeeded"} and recovery:
        db.update_recovery_event_fields(account_id, recovery["recovery_event_id"], {
            "fee_status": "paid", "fee_settled_at": datetime.now(timezone.utc).isoformat(),
            "fee_invoice_id": invoice_id}, "stripe_webhook")
    elif event_type == "invoice.payment_failed" and recovery:
        last_error = _value(obj, "last_finalization_error", {}) or {}
        failure = {"code": _value(last_error, "code"),
                   "message": _value(last_error, "message"),
                   "attempt_count": _value(obj, "attempt_count"),
                   "next_payment_attempt": _value(obj, "next_payment_attempt")}
        db.update_recovery_event_fields(account_id, recovery["recovery_event_id"], {
            "fee_status": "payment_failed", "fee_failure": failure}, "stripe_webhook")
        billing = db.get_account_billing(account_id) or {}
        db.set_account_billing(account_id, {**billing, "card_status": "failed"})
    elif event_type in {"invoice.voided", "invoice.marked_uncollectible"} and recovery:
        db.update_recovery_event_fields(account_id, recovery["recovery_event_id"], {
            "fee_status": "uncollectible"}, "stripe_webhook")
    elif event_type in {"charge.dispute.created", "charge.dispute.closed"}:
        charge_id = _value(obj, "charge") or _value(obj, "id")
        match = db.find_recovery_event_by_fee_invoice(charge_id) if charge_id else None
        if match:
            account_id, recovery = match
            dispute = {"status": _value(obj, "status"), "reason": _value(obj, "reason"),
                       "amount": _value(obj, "amount")}
            fields = {"fee_dispute": dispute}
            if event_type == "charge.dispute.created":
                fields["fee_status"] = "disputed"
            elif _value(obj, "status") == "lost":
                fields["fee_status"] = "dispute_lost"
            elif _value(obj, "status") == "won":
                fields["fee_status"] = "paid"
            db.update_recovery_event_fields(account_id, recovery["recovery_event_id"], fields,
                                            "stripe_webhook")
    elif event_type == "credit_note.created":
        credit_id = _value(obj, "id")
        match = db.find_recovery_event_by_credit_note(credit_id) if credit_id else None
        if match:
            account_id, recovery = match
            db.update_recovery_event_fields(account_id, recovery["recovery_event_id"], {
                "fee_status": "adjusted"}, "stripe_webhook")
    elif event_type in {"setup_intent.succeeded", "payment_method.detached"}:
        customer_id = _value(obj, "customer")
        account_id = db.find_account_by_stripe_customer(customer_id) if customer_id else None
        if account_id:
            billing = db.get_account_billing(account_id) or {}
            fields = {"card_on_file": event_type == "setup_intent.succeeded",
                      "card_status": "active" if event_type == "setup_intent.succeeded" else "detached"}
            if event_type == "payment_method.detached":
                fields["payment_method_id"] = None
            db.set_account_billing(account_id, {**billing, **fields})
    else:
        return {"status": "ignored"}
    _audit(account_id, event)
    return {"status": "handled", "type": event_type}


def construct_and_handle(payload: bytes, sig_header: str | None) -> dict:
    secret = os.getenv("RECOUP_STRIPE_WEBHOOK_SECRET")
    if not secret:
        return {"status": "needs_config"}
    import stripe
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Invalid Stripe webhook signature: {exc}") from exc
    event_id = _event_id(event)
    if not db.save_stripe_webhook_event(event_id, {"type": _event_type(event)}):
        return {"status": "duplicate"}
    return handle_event(event)
