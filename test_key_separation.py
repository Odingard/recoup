from __future__ import annotations

import inspect
import os

import pytest

from recoup_agent.billing import connector_keys, recoup_billing, stripe_provider
from recoup_agent.billing.stripe_provider import StripeBillingProvider
from recoup_agent.security import assert_key_separation


def test_keys_are_separate(monkeypatch):
    # Billing (write) key must never equal the connector (read-only) key.
    monkeypatch.setenv("RECOUP_BILLING_STRIPE_API_KEY", "rk_same")
    monkeypatch.setenv("RECOUP_CONNECTOR_TEST_STRIPE_API_KEY", "rk_same")
    with pytest.raises(RuntimeError):
        assert_key_separation()

    monkeypatch.setenv("RECOUP_BILLING_STRIPE_API_KEY", "sk_live_billing")
    monkeypatch.setenv("RECOUP_CONNECTOR_TEST_STRIPE_API_KEY", "rk_connector")
    assert_key_separation()

    # A full-access key used for billing alone (no connector key) is allowed.
    monkeypatch.delenv("RECOUP_CONNECTOR_TEST_STRIPE_API_KEY", raising=False)
    monkeypatch.setenv("STRIPE", "sk_live_billing")
    monkeypatch.setenv("RECOUP_BILLING_STRIPE_API_KEY", "sk_live_billing")
    assert_key_separation()


def test_billing_uses_billing_key(monkeypatch):
    monkeypatch.delenv("RECOUP_BILLING_STRIPE_API_KEY", raising=False)
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    monkeypatch.delenv("STRIPE", raising=False)
    monkeypatch.delenv("RECOUP_CONNECTOR_TEST_STRIPE_API_KEY", raising=False)

    monkeypatch.setenv("RECOUP_BILLING_STRIPE_API_KEY", "RECOUP_BILLING_STRIPE_API_KEY=sk_test_billing")
    assert recoup_billing._billing_key() == "sk_test_billing"
    assert connector_keys.resolve_connector_key(None) is None


def test_no_cross_use():
    billing_source = inspect.getsource(recoup_billing)
    connector_source = inspect.getsource(connector_keys)
    stripe_provider_source = inspect.getsource(stripe_provider)

    assert "STRIPE_API_KEY" not in billing_source.replace("RECOUP_BILLING_STRIPE_API_KEY", "")
    assert "STRIPE=" not in billing_source
    assert "RECOUP_CONNECTOR_TEST_STRIPE_API_KEY" not in billing_source

    assert "RECOUP_BILLING_STRIPE_API_KEY" not in connector_source
    assert "RECOUP_BILLING_STRIPE_API_KEY" not in stripe_provider_source


def test_read_only_cannot_write(monkeypatch):
    key = os.getenv("RECOUP_CONNECTOR_TEST_STRIPE_API_KEY")
    if not key:
        pytest.skip("RECOUP_CONNECTOR_TEST_STRIPE_API_KEY not set")

    import stripe

    stripe.api_key = key
    with pytest.raises((stripe.error.PermissionError, stripe.error.AuthenticationError, stripe.error.StripeError)):
        stripe.Customer.create(email="test@example.com")
    with pytest.raises((stripe.error.PermissionError, stripe.error.AuthenticationError, stripe.error.StripeError)):
        stripe.InvoiceItem.create(customer="cus_test", amount=100, currency="usd")
