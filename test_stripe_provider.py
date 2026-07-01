import sys
import types
from datetime import datetime, timezone

from recoup_agent.billing.models import NormalizedInvoice, NormalizedUsage
from recoup_agent.billing.stripe_provider import StripeBillingProvider, map_stripe_billing_to_reconcile_inputs


def _ts(year, month, day):
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp())


class _Collection:
    def __init__(self, rows):
        self._rows = rows

    def auto_paging_iter(self):
        return iter(self._rows)


def _install_fake_stripe(monkeypatch):
    customers = [
        types.SimpleNamespace(id="cus_1", name="Acme Corp", email="billing@acme.test"),
    ]

    subscription_item = types.SimpleNamespace(
        id="si_metered_1",
        price=types.SimpleNamespace(
            id="price_metered_1",
            nickname="Metered API",
            recurring=types.SimpleNamespace(usage_type="metered"),
        ),
    )
    subscription = types.SimpleNamespace(
        id="sub_1",
        customer="cus_1",
        status="active",
        current_period_start=_ts(2026, 6, 1),
        current_period_end=_ts(2026, 7, 1),
        items=types.SimpleNamespace(data=[subscription_item]),
    )

    invoice = types.SimpleNamespace(
        id="in_1",
        customer="cus_1",
        status="paid",
        created=_ts(2026, 6, 20),
        period_start=_ts(2026, 6, 1),
        period_end=_ts(2026, 7, 1),
        amount_paid=12000,
        total=12000,
        currency="usd",
        lines=types.SimpleNamespace(
            data=[
                types.SimpleNamespace(
                    amount=7000,
                    description="Base subscription fee",
                    currency="usd",
                    type="subscription",
                    price=types.SimpleNamespace(
                        id="price_base_1",
                        nickname="Base Plan",
                        recurring=types.SimpleNamespace(usage_type="licensed"),
                    ),
                    period=types.SimpleNamespace(start=_ts(2026, 6, 1), end=_ts(2026, 7, 1)),
                    discount_amounts=[],
                    discount=None,
                ),
                types.SimpleNamespace(
                    amount=2000,
                    description="Usage overage",
                    currency="usd",
                    type="invoiceitem",
                    price=types.SimpleNamespace(
                        id="price_metered_1",
                        nickname="Metered API",
                        recurring=types.SimpleNamespace(usage_type="metered"),
                    ),
                    period=types.SimpleNamespace(start=_ts(2026, 6, 1), end=_ts(2026, 7, 1)),
                    discount_amounts=[],
                    discount=None,
                ),
            ]
        ),
        discounts=[
            types.SimpleNamespace(
                id="di_1",
                coupon=types.SimpleNamespace(id="coupon_1", name="Launch Promo"),
            )
        ],
        total_discount_amounts=[types.SimpleNamespace(discount="di_1", amount=500)],
    )

    class Customer:
        @staticmethod
        def retrieve(customer_id):
            return customers[0] if customer_id == "cus_1" else None

        @staticmethod
        def list(limit=100):
            return _Collection(customers)

    class Subscription:
        @staticmethod
        def list(customer=None, status="all", limit=100):
            return _Collection([subscription] if customer == "cus_1" else [])

    class SubscriptionItem:
        @staticmethod
        def list_usage_record_summaries(item_id, limit=100):
            if item_id != "si_metered_1":
                return _Collection([])
            return _Collection([types.SimpleNamespace(timestamp=_ts(2026, 6, 15), total_usage=123)])

    class Invoice:
        @staticmethod
        def list(customer=None, limit=100):
            return _Collection([invoice] if customer == "cus_1" else [])

    class Discount:
        @staticmethod
        def retrieve(discount_id):
            return types.SimpleNamespace(
                id=discount_id,
                coupon=types.SimpleNamespace(id="coupon_1", name="Launch Promo"),
            )

    fake_stripe = types.SimpleNamespace(
        api_key=None,
        Customer=Customer,
        Subscription=Subscription,
        SubscriptionItem=SubscriptionItem,
        Invoice=Invoice,
        Discount=Discount,
    )
    monkeypatch.setitem(sys.modules, "stripe", fake_stripe)
    return fake_stripe


def test_stripe_provider_methods_and_adapter(monkeypatch):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_fake")
    _install_fake_stripe(monkeypatch)

    provider = StripeBillingProvider()

    customer = provider.get_customer("cus_1")
    assert customer.customer_id == "cus_1"
    assert customer.customer_name == "Acme Corp"
    assert customer.email == "billing@acme.test"

    customers = provider.list_customers()
    assert [c.customer_id for c in customers] == ["cus_1"]

    subs = provider.get_subscriptions("cus_1")
    assert subs[0].plan_name == "Metered API"
    assert subs[0].status == "active"
    assert subs[0].start_date.isoformat() == "2026-06-01"
    assert subs[0].end_date.isoformat() == "2026-07-01"

    usage = provider.get_usage("cus_1", "2026-06")
    assert usage == NormalizedUsage(customer_id="cus_1", period="2026-06", total_units=123)

    invoices = provider.get_invoices("cus_1", "2026-06")
    assert len(invoices) == 1
    assert invoices[0].amount_billed == 120.0
    assert invoices[0].line_items[0]["line_role"] == "base"
    assert invoices[0].line_items[1]["line_role"] == "overage"
    assert invoices[0].line_items[2]["line_role"] == "discount"

    usage_dict, invoice_dict, needs_review = map_stripe_billing_to_reconcile_inputs(
        "cus_1",
        "Acme Corp",
        "2026-06",
        usage,
        invoices,
    )
    assert usage_dict["units"] == 123
    assert invoice_dict["base_charge"] == 70.0
    assert invoice_dict["overage_charge"] == 20.0
    assert invoice_dict["discounts_applied"] == [{"name": "Launch Promo", "amount": 5.0}]
    assert needs_review == []


def test_stripe_adapter_flags_ambiguous_lines(monkeypatch):
    usage = NormalizedUsage(customer_id="cus_1", period="2026-06", total_units=0)
    invoices = [
        NormalizedInvoice(
            invoice_id="in_ambiguous",
            customer_id="cus_1",
            period="2026-06",
            amount_billed=10.0,
            status="paid",
            line_items=[{"amount": 1000, "description": "Mystery charge", "line_role": "unknown"}],
        )
    ]

    usage_dict, invoice_dict, needs_review = map_stripe_billing_to_reconcile_inputs(
        "cus_1",
        "Acme Corp",
        "2026-06",
        usage,
        invoices,
    )
    assert usage_dict["units"] == 0
    assert invoice_dict["base_charge"] == 0.0
    assert invoice_dict["overage_charge"] == 0.0
    assert needs_review and needs_review[0]["term"] == "invoice_line_item"
