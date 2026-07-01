from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import List, Optional

from .models import NormalizedCustomer, NormalizedSubscription, NormalizedUsage, NormalizedInvoice
from .provider import BillingProvider


def _value(obj, key, default=None):
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _date_from_ts(value) -> date:
    return datetime.fromtimestamp(int(value), tz=timezone.utc).date()


def _period_bounds(period: str) -> tuple[date, date]:
    year, month = map(int, period.split("-"))
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    return start, end


def _in_period(ts: int | None, period: str) -> bool:
    if ts is None:
        return False
    start, end = _period_bounds(period)
    day = _date_from_ts(ts)
    return start <= day < end


def _customer_name(customer) -> str:
    return _value(customer, "name") or _value(customer, "description") or _value(customer, "email") or _value(customer, "id")


def _customer_email(customer) -> str | None:
    return _value(customer, "email")


def _plan_name_from_item(item) -> str:
    price = _value(item, "price")
    nickname = _value(price, "nickname")
    if nickname:
        return nickname
    product = _value(price, "product")
    if isinstance(product, dict):
        return product.get("name") or product.get("id") or _value(price, "id", "unknown")
    if product:
        return str(product)
    plan = _value(item, "plan")
    if isinstance(plan, dict):
        return plan.get("nickname") or plan.get("id") or _value(price, "id", "unknown")
    return _value(price, "id") or _value(item, "id", "unknown")


def _classify_subscription_item(item) -> tuple[str, str]:
    price = _value(item, "price")
    recurring = _value(price, "recurring")
    usage_type = _value(recurring, "usage_type") or _value(_value(item, "plan"), "usage_type")
    if usage_type == "metered":
        return "metered", "metered"
    if usage_type == "licensed":
        return "licensed", "licensed"
    return "unknown", "unknown"


def _iter_collection(collection):
    if collection is None:
        return []
    if hasattr(collection, "auto_paging_iter"):
        return collection.auto_paging_iter()
    return collection


def _stripe():
    import stripe  # lazy import so sample mode stays offline

    key = os.getenv("STRIPE_API_KEY") or os.getenv("STRIPE")
    if not key:
        return None
    for prefix in ("STRIPE_API_KEY=", "STRIPE="):
        if key.startswith(prefix):
            key = key[len(prefix):]
            break
    stripe.api_key = key
    return stripe


def _retrieve_discount_name(stripe, discount_id: str) -> str | None:
    if not discount_id:
        return None
    try:
        discount = stripe.Discount.retrieve(discount_id)
    except Exception:
        return None
    coupon = _value(discount, "coupon")
    if isinstance(coupon, dict):
        return coupon.get("name") or coupon.get("id")
    return _value(coupon, "name") or _value(coupon, "id") or _value(discount, "id")


class StripeBillingProvider(BillingProvider):
    def __init__(self):
        self._stripe = None

    def _client(self):
        if self._stripe is None:
            self._stripe = _stripe()
        return self._stripe

    def get_customer(self, customer_id: str) -> Optional[NormalizedCustomer]:
        stripe = self._client()
        if stripe is None:
            return None
        try:
            customer = stripe.Customer.retrieve(customer_id)
        except Exception:
            return None
        return NormalizedCustomer(
            customer_id=_value(customer, "id", customer_id),
            customer_name=_customer_name(customer),
            email=_customer_email(customer),
        )

    def list_customers(self) -> List[NormalizedCustomer]:
        stripe = self._client()
        if stripe is None:
            return []
        customers = stripe.Customer.list(limit=100)
        rows: list[NormalizedCustomer] = []
        try:
            for customer in _iter_collection(customers):
                rows.append(NormalizedCustomer(
                    customer_id=_value(customer, "id"),
                    customer_name=_customer_name(customer),
                    email=_customer_email(customer),
                ))
        except Exception:
            return []
        return rows

    def get_subscriptions(self, customer_id: str) -> List[NormalizedSubscription]:
        stripe = self._client()
        if stripe is None:
            return []
        subscriptions = stripe.Subscription.list(customer=customer_id, status="all", limit=100)
        rows: list[NormalizedSubscription] = []
        try:
            for sub in _iter_collection(subscriptions):
                start_ts = _value(sub, "current_period_start") or _value(sub, "start_date")
                if not start_ts:
                    continue
                start = _date_from_ts(start_ts)
                end_ts = _value(sub, "current_period_end") or _value(sub, "ended_at")
                items = _value(sub, "items")
                item_rows = _value(items, "data") if items is not None else []
                plan_name = "unknown"
                if item_rows:
                    plan_name = _plan_name_from_item(item_rows[0])
                rows.append(NormalizedSubscription(
                    subscription_id=_value(sub, "id"),
                    customer_id=customer_id,
                    plan_name=plan_name,
                    status=_value(sub, "status", "unknown"),
                    start_date=start,
                    end_date=_date_from_ts(end_ts) if end_ts else None,
                ))
        except Exception:
            return []
        return rows

    def get_usage(self, customer_id: str, period: str) -> NormalizedUsage:
        stripe = self._client()
        if stripe is None:
            return NormalizedUsage(customer_id=customer_id, period=period, total_units=0)
        total_units = 0
        try:
            subscriptions = stripe.Subscription.list(customer=customer_id, status="all", limit=100)
            for sub in _iter_collection(subscriptions):
                items = _value(sub, "items")
                for item in (_value(items, "data") if items is not None else []):
                    _, usage_kind = _classify_subscription_item(item)
                    if usage_kind != "metered":
                        continue
                    item_id = _value(item, "id")
                    if not item_id:
                        continue
                    summaries = None
                    if hasattr(stripe.SubscriptionItem, "list_usage_record_summaries"):
                        try:
                            summaries = stripe.SubscriptionItem.list_usage_record_summaries(item_id, limit=100)
                        except TypeError:
                            summaries = stripe.SubscriptionItem.list_usage_record_summaries(subscription_item=item_id, limit=100)
                    elif hasattr(stripe, "UsageRecordSummary") and hasattr(stripe.UsageRecordSummary, "list"):
                        summaries = stripe.UsageRecordSummary.list(subscription_item=item_id, limit=100)
                    for summary in _iter_collection(summaries):
                        ts = _value(summary, "timestamp")
                        if period and ts and not _in_period(ts, period):
                            continue
                        total_units += int(_value(summary, "total_usage", 0))
        except Exception:
            return NormalizedUsage(customer_id=customer_id, period=period, total_units=0)
        return NormalizedUsage(customer_id=customer_id, period=period, total_units=total_units)

    def get_invoices(self, customer_id: str, period: str) -> List[NormalizedInvoice]:
        stripe = self._client()
        if stripe is None:
            return []
        start, end = _period_bounds(period)
        rows: list[NormalizedInvoice] = []
        try:
            invoices = stripe.Invoice.list(customer=customer_id, limit=100)
            for invoice in _iter_collection(invoices):
                created = _value(invoice, "created")
                period_start = _value(invoice, "period_start")
                period_end = _value(invoice, "period_end")
                if created is not None and not _in_period(created, period):
                    if period_start is not None and period_end is not None:
                        inv_start = _date_from_ts(period_start)
                        inv_end = _date_from_ts(period_end)
                        if not (inv_start < end and inv_end >= start):
                            continue
                    elif period_start is not None:
                        if _date_from_ts(period_start) < start or _date_from_ts(period_start) >= end:
                            continue
                    else:
                        continue

                line_items = []
                lines = _value(_value(invoice, "lines"), "data") or []
                for line in lines:
                    price = _value(line, "price")
                    recurring = _value(price, "recurring")
                    usage_type = _value(recurring, "usage_type") or _value(_value(line, "plan"), "usage_type")
                    line_role = "unknown"
                    if usage_type == "licensed" or _value(line, "type") == "subscription":
                        line_role = "base"
                    elif usage_type == "metered":
                        line_role = "overage"
                    elif _value(line, "amount", 0) < 0:
                        line_role = "discount"
                    elif _value(line, "discount_amounts"):
                        line_role = "discount"
                    elif "discount" in (_value(line, "description", "") or "").lower():
                        line_role = "discount"
                    line_items.append({
                        "amount": _value(line, "amount", 0),
                        "description": _value(line, "description", ""),
                        "currency": _value(line, "currency"),
                        "line_role": line_role,
                        "usage_type": usage_type or "",
                        "line_type": _value(line, "type", ""),
                        "price_id": _value(price, "id"),
                        "price_nickname": _value(price, "nickname"),
                        "discount_amounts": _value(line, "discount_amounts", []),
                        "period_start": _value(_value(line, "period"), "start"),
                        "period_end": _value(_value(line, "period"), "end"),
                        "discount": _value(line, "discount"),
                    })

                top_level_discounts = _value(invoice, "discounts") or []
                for discount in top_level_discounts:
                    coupon = _value(discount, "coupon")
                    coupon_name = _value(coupon, "name") if not isinstance(coupon, dict) else coupon.get("name")
                    discount_name = coupon_name or _value(coupon, "id") or _value(discount, "id")
                    if not discount_name:
                        discount_name = _retrieve_discount_name(stripe, _value(discount, "id"))
                    amount = 0
                    for discount_amount in _value(invoice, "total_discount_amounts") or []:
                        if _value(discount_amount, "discount") == _value(discount, "id"):
                            amount = int(_value(discount_amount, "amount", 0))
                            break
                    line_items.append({
                        "amount": -abs(amount),
                        "description": discount_name or "applied discount",
                        "currency": _value(invoice, "currency"),
                        "line_role": "discount",
                        "usage_type": "",
                        "line_type": "discount",
                        "discount_name": discount_name,
                        "discount_id": _value(discount, "id"),
                        "coupon": coupon,
                    })

                rows.append(NormalizedInvoice(
                    invoice_id=_value(invoice, "id"),
                    customer_id=customer_id,
                    period=period,
                    amount_billed=float(_value(invoice, "amount_paid", _value(invoice, "total", 0))) / 100.0,
                    status=_value(invoice, "status", "unknown"),
                    line_items=line_items,
                ))
        except Exception:
            return []
        return rows


def map_stripe_billing_to_reconcile_inputs(
    customer_id: str,
    customer_name: str,
    period: str,
    usage: NormalizedUsage,
    invoices: List[NormalizedInvoice],
) -> tuple[dict, dict, list[dict]]:
    needs_review: list[dict] = []
    usage_dict = {
        "customer_id": customer_id,
        "customer_name": customer_name,
        "period": period,
        "units": int(usage.total_units),
    }
    invoice_dict = {
        "customer_id": customer_id,
        "customer_name": customer_name,
        "period": period,
        "base_charge": 0.0,
        "overage_charge": 0.0,
        "discounts_applied": [],
    }

    period_invoices = [invoice for invoice in invoices if invoice.period == period]
    if not period_invoices:
        needs_review.append({
            "customer_id": customer_id,
            "customer_name": customer_name,
            "term": "billing_period",
            "reason": f"no Stripe invoices found for period {period}",
        })

    for invoice in period_invoices:
        for line in invoice.line_items:
            role = line.get("line_role", "unknown")
            amount = abs(float(line.get("amount", 0))) / 100.0
            description = line.get("description") or line.get("price_nickname") or line.get("price_id") or "Stripe line item"
            if role == "base":
                invoice_dict["base_charge"] += amount
            elif role == "overage":
                invoice_dict["overage_charge"] += amount
            elif role == "discount":
                discount_name = line.get("discount_name") or line.get("description") or line.get("coupon_name")
                if not discount_name or amount <= 0:
                    needs_review.append({
                        "customer_id": customer_id,
                        "customer_name": customer_name,
                        "term": "discounts_applied",
                        "reason": f"discount line item could not be mapped cleanly: {description}",
                    })
                    continue
                invoice_dict["discounts_applied"].append({"name": discount_name, "amount": amount})
            else:
                needs_review.append({
                    "customer_id": customer_id,
                    "customer_name": customer_name,
                    "term": "invoice_line_item",
                    "reason": f"ambiguous Stripe line item could not be classified: {description}",
                })

    return usage_dict, invoice_dict, needs_review
