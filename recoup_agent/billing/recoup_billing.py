"""Recoup's OWN billing of its 20% success fee.

This is intentionally isolated from the read-only customer Stripe connector in
``stripe_provider.py``. The customer's Stripe account is only ever read; Recoup's
own fee is charged through a SEPARATE Stripe account whose secret key is provided
via ``RECOUP_BILLING_STRIPE_API_KEY``. The two keys must never be shared.
"""
from __future__ import annotations

import os

RECOUP_BILLING_KEY_ENV = "RECOUP_BILLING_STRIPE_API_KEY"


def _billing_key() -> str | None:
    raw = os.getenv(RECOUP_BILLING_KEY_ENV)
    if not raw:
        return None
    for prefix in (f"{RECOUP_BILLING_KEY_ENV}=", "STRIPE_API_KEY=", "STRIPE="):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    return raw.strip() or None


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
                "(a SEPARATE Stripe account from the read-only customer connector)."
            ),
        }
    if amount_dollars <= 0:
        return {"status": "skipped", "message": "No recovered dollars to bill for this period."}

    try:
        import stripe

        stripe.api_key = key
        customer = (
            stripe.Customer.create(email=customer_email)
            if customer_email
            else stripe.Customer.create()
        )
        amount_cents = int(round(amount_dollars * 100))
        stripe.InvoiceItem.create(
            customer=customer.id,
            amount=amount_cents,
            currency="usd",
            description=description or f"Recoup success fee ({current_month})",
        )
        invoice = stripe.Invoice.create(
            customer=customer.id,
            collection_method="send_invoice",
            days_until_due=14,
            description=f"Recoup success fee — {current_month}",
        )
        invoice = stripe.Invoice.finalize_invoice(invoice.id)
        return {
            "status": "success",
            "invoice_id": invoice.id,
            "hosted_invoice_url": getattr(invoice, "hosted_invoice_url", None),
            "amount": round(amount_dollars, 2),
            "current_month": current_month,
        }
    except Exception as exc:  # never bubble a 500 to the operator
        return {"status": "error", "message": f"Recoup billing failed: {exc}"}
