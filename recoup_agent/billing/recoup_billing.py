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

RECOUP_BILLING_KEY_ENV = "RECOUP_BILLING_STRIPE_API_KEY"
SUCCESS_FEE_PCT = 0.20


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
    return {
        "configured": is_configured(),
        "card_on_file": bool(billing.get("payment_method_id")),
        "card_brand": billing.get("card_brand"),
        "card_last4": billing.get("card_last4"),
        "card_on_file_at": billing.get("card_on_file_at"),
        "success_fee_pct": SUCCESS_FEE_PCT,
    }


def charge_success_fee_for_finding(account_id: str, finding: dict, paid_amount: float) -> dict:
    """Charge the stored card 20% of a just-recovered amount. Never raises."""
    fee = round(paid_amount * SUCCESS_FEE_PCT, 2)
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
            amount=int(round(fee * 100)),
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
