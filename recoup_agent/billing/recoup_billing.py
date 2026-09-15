"""Recoup's own 20% success-fee billing.

This module reads only the dedicated billing key from
``RECOUP_BILLING_STRIPE_API_KEY``. It never falls back to any connector or legacy
Stripe environment variable, and it never raises on missing configuration or
Stripe API failures.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from .. import db as _db
from ..money import quantize, to_cents

RECOUP_BILLING_KEY_ENV = "RECOUP_BILLING_STRIPE_API_KEY"
SUCCESS_FEE_PCT = 0.20
TERMS_VERSION = "2026-09"


def _strip_prefix(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip()
    prefix = f"{RECOUP_BILLING_KEY_ENV}="
    if value.startswith(prefix):
        value = value[len(prefix):].strip()
    return value or None


def _billing_key() -> str | None:
    return _strip_prefix(os.getenv(RECOUP_BILLING_KEY_ENV))


def is_configured() -> bool:
    return _billing_key() is not None


def create_success_fee_invoice(
    *,
    customer_email: str | None,
    amount_dollars: float,
    current_month: str,
    description: str | None = None,
) -> dict:
    """Create and finalize a Stripe invoice for Recoup's success fee.

    Returns a structured result rather than raising, so callers never 500 on a
    misconfigured or empty billing account.
    """
    key = _billing_key()
    if key is None:
        return {
            "status": "needs_config",
            "message": (
                "Recoup billing is not configured. Set RECOUP_BILLING_STRIPE_API_KEY "
                "to a dedicated billing key for Recoup's own Stripe account."
            ),
        }
    if amount_dollars <= 0:
        return {"status": "skipped", "message": "No recovered dollars to bill for this period."}

    try:
        import stripe

        # Pass the billing key explicitly on every call rather than mutating the
        # process-wide ``stripe.api_key``, so concurrent requests can never have
        # this write key clobbered by (or clobber) a per-tenant connector key.
        customer = (
            stripe.Customer.create(email=customer_email, api_key=key)
            if customer_email
            else stripe.Customer.create(api_key=key)
        )
        amount_cents = to_cents(amount_dollars)
        invoice = stripe.Invoice.create(
            customer=customer.id,
            collection_method="send_invoice",
            days_until_due=14,
            description=f"Recoup success fee — {current_month}",
            api_key=key,
        )
        stripe.InvoiceItem.create(
            customer=customer.id,
            invoice=invoice.id,
            amount=amount_cents,
            currency="usd",
            description=description or f"Recoup success fee ({current_month})",
            api_key=key,
        )
        invoice = stripe.Invoice.finalize_invoice(invoice.id, api_key=key)
        return {
            "status": "success",
            "invoice_id": invoice.id,
            "hosted_invoice_url": getattr(invoice, "hosted_invoice_url", None),
            "amount": quantize(amount_dollars),
            "current_month": current_month,
        }
    except Exception as exc:  # never bubble a 500 to the operator
        return {"status": "error", "message": f"Recoup billing failed: {exc}"}


def _needs_config() -> dict:
    return {
        "status": "needs_config",
        "message": (
            "Recoup billing is not configured. Set RECOUP_BILLING_STRIPE_API_KEY "
            "to a dedicated billing key for Recoup's own Stripe account."
        ),
    }


def get_or_create_billing_customer(account_id: str, email: str | None) -> str:
    """Return the tenant's Stripe customer id on Recoup's own account."""
    billing = _db.get_account_billing(account_id) or {}
    if billing.get("stripe_customer_id"):
        return billing["stripe_customer_id"]
    import stripe
    key = _billing_key()
    customer = stripe.Customer.create(
        email=email, metadata={"recoup_account_id": account_id}, api_key=key)
    _db.set_account_billing(account_id, {**billing, "stripe_customer_id": customer.id})
    return customer.id


def create_setup_checkout_url(account_id: str, email: str | None, base_url: str) -> dict:
    """Hosted Stripe Checkout in setup mode — collects a card without charging."""
    key = _billing_key()
    if key is None:
        return _needs_config()
    try:
        import stripe
        cust_id = get_or_create_billing_customer(account_id, email)
        base = base_url.rstrip("/")
        session = stripe.checkout.Session.create(
            mode="setup",
            customer=cust_id,
            payment_method_types=["card"],
            success_url=f"{base}/app/?billing_setup={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{base}/app/?billing_setup=cancelled",
            api_key=key,
        )
        return {"status": "success", "url": session.url}
    except Exception as exc:
        return {"status": "error", "message": f"Could not start card setup: {exc}"}


def complete_setup_session(account_id: str, session_id: str) -> dict:
    """Attach the just-collected card as the customer's default payment method."""
    key = _billing_key()
    if key is None:
        return _needs_config()
    try:
        import stripe
        session = stripe.checkout.Session.retrieve(
            session_id, expand=["setup_intent.payment_method"], api_key=key)
        billing = _db.get_account_billing(account_id) or {}
        if session.customer != billing.get("stripe_customer_id"):
            return {"status": "error", "message": "Setup session does not match this account."}
        if session.status != "complete":
            return {"status": "error", "message": f"Setup session is {session.status}, not complete."}
        pm = session.setup_intent.payment_method
        stripe.Customer.modify(
            billing["stripe_customer_id"],
            invoice_settings={"default_payment_method": pm.id},
            api_key=key,
        )
        card = getattr(pm, "card", None)
        billing.update({
            "payment_method_id": pm.id,
            "card_brand": getattr(card, "brand", None),
            "card_last4": getattr(card, "last4", None),
            "card_on_file_at": datetime.now(timezone.utc).isoformat(),
        })
        _db.set_account_billing(account_id, billing)
        return {"status": "success", "billing": billing_status(account_id)}
    except Exception as exc:
        return {"status": "error", "message": f"Could not save the payment method: {exc}"}


def billing_status(account_id: str) -> dict:
    billing = _db.get_account_billing(account_id) or {}
    try:
        acceptance = _db.get_terms_acceptance(account_id)
    except Exception:
        acceptance = None
    return {
        "configured": is_configured(),
        "card_on_file": bool(billing.get("payment_method_id")),
        "card_brand": billing.get("card_brand"),
        "card_last4": billing.get("card_last4"),
        "card_on_file_at": billing.get("card_on_file_at"),
        "card_status": billing.get("card_status"),
        "success_fee_pct": SUCCESS_FEE_PCT,
        "terms_accepted": ({"version": acceptance.get("version"),
                             "accepted_at": acceptance.get("accepted_at")}
                            if acceptance else None),
    }


def charge_success_fee_for_finding(account_id: str, finding: dict, paid_amount: float) -> dict:
    """Legacy: charge 20% of a lump recovered amount. Kept for compatibility;
    the API now charges per realization event (charge_success_fee_for_event)."""
    fee = quantize(paid_amount * SUCCESS_FEE_PCT)
    finding_id = finding.get("finding_id", "unknown")
    key = _billing_key()
    if key is None:
        return _needs_config()
    billing = _db.get_account_billing(account_id) or {}
    cust_id = billing.get("stripe_customer_id")
    if not billing.get("payment_method_id") or not cust_id:
        return {"status": "unbilled", "message": "No payment method on file."}
    try:
        import stripe
        invoice = stripe.Invoice.create(
            customer=cust_id,
            collection_method="charge_automatically",
            auto_advance=True,
            description=(f"Recoup success fee — {finding.get('customer_name', '')} "
                         f"{finding.get('period', '')}"),
            metadata={"finding_id": finding_id, "recoup_account_id": account_id},
            idempotency_key=f"fee-{account_id}-{finding_id}",
            api_key=key,
        )
        stripe.InvoiceItem.create(
            customer=cust_id,
            invoice=invoice.id,
            amount=to_cents(fee),
            currency="usd",
            description=(f"Recoup success fee ({SUCCESS_FEE_PCT:.0%} of "
                         f"${paid_amount:,.2f} recovered — {finding.get('customer_name', '')})"),
            api_key=key,
        )
        invoice = stripe.Invoice.finalize_invoice(invoice.id, api_key=key)
        invoice = stripe.Invoice.pay(invoice.id, api_key=key)
        return {
            "status": "paid" if getattr(invoice, "status", None) == "paid" else "pending",
            "invoice_id": invoice.id,
            "amount": fee,
            "hosted_invoice_url": getattr(invoice, "hosted_invoice_url", None),
            "charged_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {"status": "error", "message": f"Recoup billing failed: {exc}"}


def charge_success_fee_for_event(account_id: str, finding: dict, event) -> dict:
    """Charge the stored card the fee on one realization event
    (event.fee_amount). Idempotent per (account, finding, event). Never raises."""
    fee = quantize(event.fee_amount or 0)
    finding_id = finding.get("finding_id", "unknown")
    key = _billing_key()
    if key is None:
        return _needs_config()
    try:
        accepted = _db.get_terms_acceptance(account_id)
    except Exception:
        accepted = None
    if not accepted:
        return {"status": "terms_missing", "message": "Accept the Terms of Service before billing a success fee."}
    billing = _db.get_account_billing(account_id) or {}
    cust_id = billing.get("stripe_customer_id")
    if not billing.get("payment_method_id") or not cust_id:
        return {"status": "unbilled", "message": "No payment method on file."}
    try:
        import stripe
        invoice = stripe.Invoice.create(
            customer=cust_id,
            collection_method="charge_automatically",
            auto_advance=True,
            description=(f"Recoup success fee — {finding.get('customer_name', '')} "
                         f"{finding.get('period', '')}"),
            metadata={"finding_id": finding_id,
                      "recoup_account_id": account_id,
                      "recovery_event_id": event.recovery_event_id,
                      "recovery_basis": event.recovery_basis},
            idempotency_key=f"fee-{account_id}-{finding_id}-{event.recovery_event_id}",
            api_key=key,
        )
        stripe.InvoiceItem.create(
            customer=cust_id,
            invoice=invoice.id,
            amount=to_cents(fee),
            currency="usd",
            description=(f"Recoup success fee ({SUCCESS_FEE_PCT:.0%} of "
                         f"${event.realized_value:,.2f} recovered "
                         f"[{event.recovery_basis}] — "
                         f"{finding.get('customer_name', '')})"),
            api_key=key,
        )
        invoice = stripe.Invoice.finalize_invoice(invoice.id, api_key=key)
        invoice = stripe.Invoice.pay(invoice.id, api_key=key)
        return {
            "status": "paid" if getattr(invoice, "status", None) == "paid" else "pending",
            "invoice_id": invoice.id,
            "fee_invoice_id": invoice.id,
            "charge_id": getattr(invoice, "charge", None),
            "amount": fee,
            "recovery_event_id": event.recovery_event_id,
            "hosted_invoice_url": getattr(invoice, "hosted_invoice_url", None),
            "charged_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        return {"status": "error", "message": f"Recoup billing failed: {exc}"}


def _field(event, key, default=None):
    if isinstance(event, dict):
        return event.get(key, default)
    return getattr(event, key, default)


def retry_success_fee(account_id: str, event) -> dict:
    """Retry one retryable fee event. Never raises. After 3 cumulative failed
    attempts the event is placed on collection_hold with a needs-review-style
    audit message."""
    status = _field(event, "fee_status")
    if status not in {"payment_failed", "error", "pending", "unbilled"}:
        return {"status": "skipped",
                "message": f"Fee status {status or 'unknown'} is not retryable."}
    retry_count = int(_field(event, "fee_retry_count", 0) or 0)
    finding_id = _field(event, "finding_id", "unknown")
    if retry_count >= 3:
        return {"status": "collection_hold",
                "message": (f"Success fee for {finding_id} could not be "
                            "collected after 3 attempts; contact the customer.")}
    try:
        accepted = _db.get_terms_acceptance(account_id)
    except Exception:
        accepted = None
    if not accepted:
        return {"status": "terms_missing",
                "message": "Accept the Terms of Service before billing a success fee."}
    billing = _db.get_account_billing(account_id) or {}
    if not billing.get("payment_method_id"):
        return {"status": "unbilled", "message": "No payment method on file."}
    key = _billing_key()
    if key is None:
        return _needs_config()
    charge = _field(event, "fee_charge", {}) or {}
    invoice_id = charge.get("invoice_id") or _field(event, "fee_invoice_id")
    try:
        if invoice_id:
            import stripe
            invoice = stripe.Invoice.pay(invoice_id, api_key=key)
            result = {"status": "paid" if getattr(invoice, "status", None) == "paid"
                      else "pending",
                      "invoice_id": invoice_id, "fee_invoice_id": invoice_id}
        else:
            import types
            finding = _field(event, "finding", None) or {"finding_id": finding_id}
            data = event if isinstance(event, dict) else vars(event)
            result = charge_success_fee_for_event(
                account_id, finding, types.SimpleNamespace(**data))
    except Exception as exc:
        result = {"status": "payment_failed", "message": str(exc)}
    result["fee_retry_count"] = retry_count + 1
    result["fee_last_retry_at"] = datetime.now(timezone.utc).isoformat()
    if result.get("status") in {"error", "payment_failed"} and retry_count + 1 >= 3:
        result["status"] = "collection_hold"
        result["message"] = (f"Success fee for {finding_id} could not be "
                             "collected after 3 attempts; contact the customer.")
    return result


def adjust_success_fee_for_reversal(account_id: str, original_event,
                                    reversal_event) -> dict:
    """Credit the fee on a reversed realization. Only paid invoices get a
    Stripe credit note, refunded to the card (a paid invoice's credit note must
    allocate its full amount to refund/credit/out-of-band); pending/unbilled
    originals stay 'adjustment_pending' for a human to void/adjust. Never raises."""
    fee_credit = abs(reversal_event.fee_amount or 0)
    charge = original_event.fee_charge or {}
    invoice_id = charge.get("invoice_id")
    if charge.get("status") != "paid" or not invoice_id:
        return {"status": "adjustment_pending",
                "message": "Original fee was not a paid invoice; no credit note issued."}
    key = _billing_key()
    if key is None:
        return _needs_config()
    try:
        import stripe
        note = stripe.CreditNote.create(
            invoice=invoice_id,
            amount=to_cents(fee_credit),
            refund_amount=to_cents(fee_credit),
            reason="order_change",
            memo=(f"Reversal of recovered value ({reversal_event.recovery_basis}) "
                  f"on finding {original_event.finding_id}: "
                  f"-${reversal_event.reversal_amount:,.2f} realized, "
                  f"-${fee_credit:,.2f} fee credited"),
            idempotency_key=f"feeadj-{account_id}-{reversal_event.recovery_event_id}",
            api_key=key,
        )
        return {"status": "adjusted", "credit_note_id": note.id,
                "amount": quantize(fee_credit)}
    except Exception as exc:
        return {"status": "error", "message": f"Fee adjustment failed: {exc}"}
