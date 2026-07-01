"""Recoup's own 20% success-fee billing.

This module reads only the dedicated billing key from
``RECOUP_BILLING_STRIPE_API_KEY``. It never falls back to any connector or legacy
Stripe environment variable, and it never raises on missing configuration or
Stripe API failures.
"""
from __future__ import annotations

import os

RECOUP_BILLING_KEY_ENV = "RECOUP_BILLING_STRIPE_API_KEY"


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
        amount_cents = int(round(amount_dollars * 100))
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
            "amount": round(amount_dollars, 2),
            "current_month": current_month,
        }
    except Exception as exc:  # never bubble a 500 to the operator
        return {"status": "error", "message": f"Recoup billing failed: {exc}"}
