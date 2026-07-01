"""Recoup's OWN billing of its 20% success fee.

Recoup's fee is charged through a Stripe account whose secret key is provided via
``RECOUP_BILLING_STRIPE_API_KEY``. In production this should be a SEPARATE account
(and key) from the read-only customer connector in ``stripe_provider.py`` so the
key used to *read* a customer's billing can never *write*.

For a single-account pilot, if ``RECOUP_BILLING_STRIPE_API_KEY`` is not set we fall
back to the ``STRIPE`` / ``STRIPE_API_KEY`` value. ``using_shared_key()`` reports
when that fallback is active so the caller can surface the caveat.
"""
from __future__ import annotations

import os

RECOUP_BILLING_KEY_ENV = "RECOUP_BILLING_STRIPE_API_KEY"
_FALLBACK_KEY_ENVS = ("STRIPE_API_KEY", "STRIPE")
_KEY_PREFIXES = (f"{RECOUP_BILLING_KEY_ENV}=", "STRIPE_API_KEY=", "STRIPE=")


def _strip_prefix(raw: str) -> str:
    for prefix in _KEY_PREFIXES:
        if raw.startswith(prefix):
            return raw[len(prefix):].strip()
    return raw.strip()


def _dedicated_key() -> str | None:
    raw = os.getenv(RECOUP_BILLING_KEY_ENV)
    return _strip_prefix(raw) or None if raw else None


def _billing_key() -> str | None:
    key = _dedicated_key()
    if key:
        return key
    for env_name in _FALLBACK_KEY_ENVS:
        raw = os.getenv(env_name)
        if raw:
            stripped = _strip_prefix(raw)
            if stripped:
                return stripped
    return None


def is_configured() -> bool:
    return _billing_key() is not None


def using_shared_key() -> bool:
    """True when billing is falling back to the read-only customer connector key
    instead of a dedicated, separate billing key."""
    return _billing_key() is not None and _dedicated_key() is None


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
        invoice = stripe.Invoice.create(
            customer=customer.id,
            collection_method="send_invoice",
            days_until_due=14,
            description=f"Recoup success fee — {current_month}",
        )
        # Attach the line item to THIS invoice explicitly. Recent Stripe API versions
        # do not auto-pull pending invoice items, so a customer-scoped item would
        # otherwise leave the invoice at $0.
        stripe.InvoiceItem.create(
            customer=customer.id,
            invoice=invoice.id,
            amount=amount_cents,
            currency="usd",
            description=description or f"Recoup success fee ({current_month})",
        )
        invoice = stripe.Invoice.finalize_invoice(invoice.id)
        return {
            "status": "success",
            "invoice_id": invoice.id,
            "hosted_invoice_url": getattr(invoice, "hosted_invoice_url", None),
            "amount": round(amount_dollars, 2),
            "current_month": current_month,
            "shared_key": using_shared_key(),
        }
    except Exception as exc:  # never bubble a 500 to the operator
        return {"status": "error", "message": f"Recoup billing failed: {exc}"}
