"""Stripe hardening: webhook signature/idempotency/state transitions, fee
retries with collection hold, Terms-of-Service gating, and sample-mode stubs."""
import sys
import types

import firebase_admin.auth as firebase_auth
import pytest
from fastapi.testclient import TestClient

from recoup_agent import api
from recoup_agent.billing import recoup_billing
from recoup_agent.billing import stripe_webhook


def _auth():
    return {"Authorization": "Bearer t1"}


def _wire_auth(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(firebase_auth, "verify_id_token",
                        lambda token, **kw: {"uid": token, "email": f"{token}@b.c"})


def _install_fake_stripe(monkeypatch, *, construct_event=None,
                         pay_status="paid", pay_raises=None):
    state = {"pays": [], "invoice_creates": [], "sessions": []}

    class Webhook:
        @staticmethod
        def construct_event(payload, sig, secret):
            if construct_event is None:
                raise ValueError("bad signature")
            return construct_event(payload, sig, secret)

    class Invoice:
        @staticmethod
        def create(customer=None, **kwargs):
            state["invoice_creates"].append(kwargs)
            return types.SimpleNamespace(
                id=f"in_{len(state['invoice_creates'])}", status="draft",
                hosted_invoice_url="https://pay.stripe.test/in")

        @staticmethod
        def finalize_invoice(iid, api_key=None):
            return types.SimpleNamespace(id=iid, status="open")

        @staticmethod
        def pay(iid, api_key=None):
            state["pays"].append(iid)
            if pay_raises:
                raise pay_raises
            return types.SimpleNamespace(
                id=iid, status=pay_status, charge="ch_paid",
                hosted_invoice_url="https://pay.stripe.test/in")

    class InvoiceItem:
        @staticmethod
        def create(**kwargs):
            return types.SimpleNamespace(id="ii_1")

    class Session:
        @staticmethod
        def create(**kwargs):
            state["sessions"].append(kwargs)
            return types.SimpleNamespace(
                id="cs_1", url="https://checkout.stripe.test/cs_1")

    class Customer:
        @staticmethod
        def create(**kwargs):
            return types.SimpleNamespace(id="cus_new")

    fake = types.SimpleNamespace(
        Webhook=Webhook, Invoice=Invoice, InvoiceItem=InvoiceItem,
        checkout=types.SimpleNamespace(Session=Session), Customer=Customer)
    monkeypatch.setitem(sys.modules, "stripe", fake)
    return state


def _event(event_id="evt_1", event_type="invoice.paid", obj=None):
    return {"id": event_id, "type": event_type,
            "data": {"object": obj or {"id": "in_1"}}}


def _wire_webhook(monkeypatch):
    """Patch the db seam used by the webhook handler; returns captured calls."""
    calls = {"saved": [], "updates": [], "billing": [], "audits": []}
    monkeypatch.setattr(api.db, "save_stripe_webhook_event",
                        lambda eid, summary: calls["saved"].append(eid) or True)
    monkeypatch.setattr(stripe_webhook.db, "save_stripe_webhook_event",
                        lambda eid, summary: calls["saved"].append(eid) or True)
    monkeypatch.setattr(stripe_webhook.db, "find_recovery_event_by_fee_invoice",
                        lambda iid: ("acct1", {"recovery_event_id": "re1",
                                               "finding_id": "f1",
                                               "fee_status": "pending"})
                        if iid == "in_1" else None)
    monkeypatch.setattr(stripe_webhook.db, "find_recovery_event_by_credit_note",
                        lambda cid: ("acct1", {"recovery_event_id": "re2",
                                               "fee_status": "adjustment_pending"})
                        if cid == "cn_1" else None)
    monkeypatch.setattr(stripe_webhook.db, "find_account_by_stripe_customer",
                        lambda cid: "acct1" if cid == "cus_1" else None)
    monkeypatch.setattr(stripe_webhook.db, "get_account_billing",
                        lambda a: {"stripe_customer_id": "cus_1",
                                   "payment_method_id": "pm_1"})

    def _set_billing(a, billing):
        calls["billing"].append(billing)
    monkeypatch.setattr(stripe_webhook.db, "set_account_billing", _set_billing)

    def _update(a, eid, fields, ev):
        calls["updates"].append((a, eid, fields, ev))
    monkeypatch.setattr(stripe_webhook.db, "update_recovery_event_fields", _update)
    monkeypatch.setattr(stripe_webhook.db, "append_assurance_audit",
                        lambda a, entry: calls["audits"].append((a, entry)))
    return calls


def test_webhook_missing_secret_503(monkeypatch):
    monkeypatch.delenv("RECOUP_STRIPE_WEBHOOK_SECRET", raising=False)
    client = TestClient(api.app)
    resp = client.post("/api/billing/stripe/webhook", content=b"{}",
                       headers={"Stripe-Signature": "t=1,v1=x"})
    assert resp.status_code == 503


def test_webhook_bad_signature_400(monkeypatch):
    monkeypatch.setenv("RECOUP_STRIPE_WEBHOOK_SECRET", "whsec_1")
    _install_fake_stripe(monkeypatch)
    client = TestClient(api.app)
    resp = client.post("/api/billing/stripe/webhook", content=b"{}",
                       headers={"Stripe-Signature": "t=1,v1=bad"})
    assert resp.status_code == 400


def test_webhook_duplicate_event_no_second_mutation(monkeypatch):
    monkeypatch.setenv("RECOUP_STRIPE_WEBHOOK_SECRET", "whsec_1")
    _install_fake_stripe(
        monkeypatch, construct_event=lambda p, s, sec: _event())
    _wire_webhook(monkeypatch)
    monkeypatch.setattr(stripe_webhook.db, "save_stripe_webhook_event",
                        lambda eid, summary: False)
    client = TestClient(api.app)
    resp = client.post("/api/billing/stripe/webhook", content=b"{}",
                       headers={"Stripe-Signature": "t=1,v1=ok"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "duplicate"


def test_invoice_paid_marks_fee_paid(monkeypatch):
    monkeypatch.setenv("RECOUP_STRIPE_WEBHOOK_SECRET", "whsec_1")
    _install_fake_stripe(
        monkeypatch, construct_event=lambda p, s, sec: _event())
    calls = _wire_webhook(monkeypatch)
    client = TestClient(api.app)
    resp = client.post("/api/billing/stripe/webhook", content=b"{}",
                       headers={"Stripe-Signature": "t=1,v1=ok"})
    assert resp.status_code == 200
    update = calls["updates"][-1][2]
    assert update["fee_status"] == "paid"
    assert update["fee_settled_at"]
    assert any(e[1]["event"] == "stripe_webhook" for e in calls["audits"])


def test_invoice_payment_failed_sets_fee_and_card(monkeypatch):
    monkeypatch.setenv("RECOUP_STRIPE_WEBHOOK_SECRET", "whsec_1")
    obj = {"id": "in_1", "attempt_count": 1, "next_payment_attempt": 123,
           "last_finalization_error": {"code": "card_declined",
                                       "message": "declined"}}
    _install_fake_stripe(
        monkeypatch,
        construct_event=lambda p, s, sec: _event(
            event_type="invoice.payment_failed", obj=obj))
    calls = _wire_webhook(monkeypatch)
    client = TestClient(api.app)
    resp = client.post("/api/billing/stripe/webhook", content=b"{}",
                       headers={"Stripe-Signature": "t=1,v1=ok"})
    assert resp.status_code == 200
    update = calls["updates"][-1][2]
    assert update["fee_status"] == "payment_failed"
    assert update["fee_failure"]["code"] == "card_declined"
    assert update["fee_failure"]["attempt_count"] == 1
    assert calls["billing"][-1]["card_status"] == "failed"


def test_dispute_created_and_closed_transitions(monkeypatch):
    monkeypatch.setenv("RECOUP_STRIPE_WEBHOOK_SECRET", "whsec_1")
    calls = _wire_webhook(monkeypatch)
    client = TestClient(api.app)

    def post(obj, event_type):
        _install_fake_stripe(
            monkeypatch,
            construct_event=lambda p, s, sec: _event(
                event_type=event_type, obj=obj))
        resp = client.post("/api/billing/stripe/webhook", content=b"{}",
                           headers={"Stripe-Signature": "t=1,v1=ok"})
        assert resp.status_code == 200

    post({"id": "dp_1", "charge": "in_1", "status": "needs_response",
          "reason": "fraudulent", "amount": 200000},
         "charge.dispute.created")
    update = calls["updates"][-1][2]
    assert update["fee_status"] == "disputed"
    assert update["fee_dispute"]["reason"] == "fraudulent"

    post({"id": "dp_1", "charge": "in_1", "status": "lost"},
         "charge.dispute.closed")
    assert calls["updates"][-1][2]["fee_status"] == "dispute_lost"

    post({"id": "dp_1", "charge": "in_1", "status": "won"},
         "charge.dispute.closed")
    assert calls["updates"][-1][2]["fee_status"] == "paid"


def test_credit_note_created_marks_reversal_adjusted(monkeypatch):
    monkeypatch.setenv("RECOUP_STRIPE_WEBHOOK_SECRET", "whsec_1")
    _install_fake_stripe(
        monkeypatch,
        construct_event=lambda p, s, sec: _event(
            event_type="credit_note.created", obj={"id": "cn_1"}))
    calls = _wire_webhook(monkeypatch)
    client = TestClient(api.app)
    resp = client.post("/api/billing/stripe/webhook", content=b"{}",
                       headers={"Stripe-Signature": "t=1,v1=ok"})
    assert resp.status_code == 200
    assert calls["updates"][-1][2]["fee_status"] == "adjusted"


def test_setup_intent_and_detach_update_card_state(monkeypatch):
    monkeypatch.setenv("RECOUP_STRIPE_WEBHOOK_SECRET", "whsec_1")
    calls = _wire_webhook(monkeypatch)
    client = TestClient(api.app)
    for event_type, expected in (
            ("setup_intent.succeeded", ("active", True)),
            ("payment_method.detached", ("detached", False))):
        _install_fake_stripe(
            monkeypatch,
            construct_event=lambda p, s, sec, et=event_type: _event(
                event_type=et, obj={"customer": "cus_1"}))
        resp = client.post("/api/billing/stripe/webhook", content=b"{}",
                           headers={"Stripe-Signature": "t=1,v1=ok"})
        assert resp.status_code == 200
        assert (calls["billing"][-1]["card_status"],
                calls["billing"][-1]["card_on_file"]) == expected


def test_unhandled_event_ignored(monkeypatch):
    monkeypatch.setenv("RECOUP_STRIPE_WEBHOOK_SECRET", "whsec_1")
    _install_fake_stripe(
        monkeypatch,
        construct_event=lambda p, s, sec: _event(
            event_type="customer.created", obj={"id": "cus_9"}))
    _wire_webhook(monkeypatch)
    client = TestClient(api.app)
    resp = client.post("/api/billing/stripe/webhook", content=b"{}",
                       headers={"Stripe-Signature": "t=1,v1=ok"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def _retry_env(monkeypatch):
    monkeypatch.setenv("RECOUP_BILLING_STRIPE_API_KEY", "sk_test_billing")
    monkeypatch.setattr(recoup_billing._db, "get_terms_acceptance",
                        lambda a: {"version": recoup_billing.TERMS_VERSION,
                                   "accepted_at": "2026-09-01"})
    monkeypatch.setattr(recoup_billing._db, "get_account_billing",
                        lambda a: {"stripe_customer_id": "cus_1",
                                   "payment_method_id": "pm_1"})


def test_retry_pays_existing_invoice_and_counts(monkeypatch):
    _retry_env(monkeypatch)
    state = _install_fake_stripe(monkeypatch)
    event = {"recovery_event_id": "re1", "finding_id": "f1",
             "fee_status": "payment_failed", "fee_retry_count": 1,
             "fee_charge": {"invoice_id": "in_99"}}
    result = recoup_billing.retry_success_fee("acct1", event)
    assert result["status"] == "paid"
    assert result["fee_retry_count"] == 2
    assert result["fee_last_retry_at"]
    assert state["pays"] == ["in_99"]
    assert state["invoice_creates"] == []


def test_retry_creates_invoice_with_same_idempotency(monkeypatch):
    _retry_env(monkeypatch)
    state = _install_fake_stripe(monkeypatch)
    event = {"recovery_event_id": "re1", "finding_id": "f1",
             "fee_status": "unbilled", "fee_retry_count": 0,
             "fee_amount": 2000.0, "realized_value": 10000.0,
             "recovery_basis": "cash_payment"}
    result = recoup_billing.retry_success_fee("acct1", event)
    assert result["status"] == "paid"
    assert result["fee_invoice_id"] == "in_1"
    assert state["invoice_creates"][0]["idempotency_key"] == "fee-acct1-f1-re1"


def test_third_failed_attempt_collection_hold_and_fourth_skipped(monkeypatch):
    _retry_env(monkeypatch)
    _install_fake_stripe(monkeypatch, pay_raises=RuntimeError("card declined"))
    event = {"recovery_event_id": "re1", "finding_id": "f1",
             "fee_status": "payment_failed", "fee_retry_count": 2,
             "fee_charge": {"invoice_id": "in_99"}}
    result = recoup_billing.retry_success_fee("acct1", event)
    assert result["status"] == "collection_hold"
    assert "contact the customer" in result["message"]

    event["fee_retry_count"] = 3
    again = recoup_billing.retry_success_fee("acct1", event)
    assert again["status"] == "collection_hold"


def test_charge_without_terms_acceptance_fails_closed(monkeypatch):
    _retry_env(monkeypatch)
    monkeypatch.setattr(recoup_billing._db, "get_terms_acceptance",
                        lambda a: None)
    state = _install_fake_stripe(monkeypatch)
    event = types.SimpleNamespace(
        recovery_event_id="re1", finding_id="f1", fee_amount=2000.0,
        realized_value=10000.0, recovery_basis="cash_payment")
    result = recoup_billing.charge_success_fee_for_event(
        "acct1", {"finding_id": "f1"}, event)
    assert result["status"] == "terms_missing"
    assert state["invoice_creates"] == []


def test_retry_without_terms_fails_closed(monkeypatch):
    _retry_env(monkeypatch)
    monkeypatch.setattr(recoup_billing._db, "get_terms_acceptance",
                        lambda a: None)
    event = {"recovery_event_id": "re1", "finding_id": "f1",
             "fee_status": "payment_failed"}
    result = recoup_billing.retry_success_fee("acct1", event)
    assert result["status"] == "terms_missing"


def test_fee_amount_unchanged_20pct(monkeypatch):
    """20% of realized value in dollars; quantization unchanged."""
    from recoup_agent.money import quantize
    assert quantize(10000.0 * recoup_billing.SUCCESS_FEE_PCT) == 2000.0
    assert recoup_billing.SUCCESS_FEE_PCT == 0.20


def _wire_setup(monkeypatch):
    captured = {}
    monkeypatch.setattr(api.recoup_billing._db, "record_terms_acceptance",
                        lambda a, record: captured.setdefault("record", record))
    monkeypatch.setattr(api.db, "record_terms_acceptance",
                        lambda a, record: captured.setdefault("record", record))
    return captured


def test_setup_session_rejects_missing_acceptance(monkeypatch):
    _wire_auth(monkeypatch)
    monkeypatch.setenv("RECOUP_BILLING_STRIPE_API_KEY", "sk_test_billing")
    _install_fake_stripe(monkeypatch)
    captured = _wire_setup(monkeypatch)
    client = TestClient(api.app)
    for body in ({}, {"accept_terms": True},
                 {"accept_terms": True, "terms_version": "2020-01"}):
        resp = client.post("/api/billing/setup-session", headers=_auth(),
                           json=body)
        assert resp.status_code == 400, body
    assert "record" not in captured


def test_setup_session_records_acceptance(monkeypatch):
    _wire_auth(monkeypatch)
    monkeypatch.setenv("RECOUP_BILLING_STRIPE_API_KEY", "sk_test_billing")
    state = _install_fake_stripe(monkeypatch)
    monkeypatch.setattr(recoup_billing._db, "get_account_billing",
                        lambda a: {"stripe_customer_id": "cus_1"})
    captured = _wire_setup(monkeypatch)
    client = TestClient(api.app)
    resp = client.post(
        "/api/billing/setup-session",
        headers={**_auth(), "X-Forwarded-For": "203.0.113.9, 10.0.0.1",
                 "User-Agent": "pytest-agent"},
        json={"accept_terms": True, "terms_version": "2026-09"})
    assert resp.status_code == 200
    record = captured["record"]
    assert record["version"] == "2026-09"
    assert record["ip"] == "203.0.113.9"
    assert record["user_agent"] == "pytest-agent"
    assert record["actor_email"] == "t1@b.c"
    assert record["accepted_at"]
    assert state["sessions"]


def test_billing_status_exposes_terms(monkeypatch):
    _wire_auth(monkeypatch)
    monkeypatch.delenv("RECOUP_BILLING_STRIPE_API_KEY", raising=False)
    monkeypatch.setattr(api.db, "get_account_billing", lambda a: {})
    monkeypatch.setattr(api.recoup_billing._db, "get_account_billing",
                        lambda a: {})
    monkeypatch.setattr(api.recoup_billing._db, "get_terms_acceptance",
                        lambda a: {"version": "2026-09",
                                   "accepted_at": "2026-09-11T00:00:00+00:00"})
    client = TestClient(api.app)
    resp = client.get("/api/billing/status", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["terms_accepted"]["version"] == "2026-09"


def test_sample_mode_stubs(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    client = TestClient(api.app)
    headers = {"X-Recoup-Sample": "1"}
    status = client.get("/api/billing/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["sample"] is True
    setup = client.post("/api/billing/setup-session", headers=headers,
                        json={})
    assert setup.status_code == 200
    assert setup.json()["mode"] == "sample"
    retry = client.post("/api/billing/retry-fee", headers=headers,
                        json={"recovery_event_id": "re1"})
    assert retry.status_code == 200
    assert retry.json()["mode"] == "sample"


def test_sync_recoveries_retries_each_retryable_once(monkeypatch):
    _wire_auth(monkeypatch)
    _retry_env(monkeypatch)
    state = _install_fake_stripe(monkeypatch)
    events = [
        {"recovery_event_id": "re1", "finding_id": "f1",
         "fee_status": "payment_failed",
         "fee_charge": {"invoice_id": "in_a"}},
        {"recovery_event_id": "re2", "finding_id": "f1",
         "fee_status": "paid",
         "fee_charge": {"invoice_id": "in_b"}},
        {"recovery_event_id": "re3", "finding_id": "f1",
         "fee_status": "pending",
         "fee_charge": {"invoice_id": "in_c"}},
    ]
    monkeypatch.setattr(api.db, "get_all_findings",
                        lambda a: [{"finding_id": "f1", "status": "recovered"}])
    monkeypatch.setattr(api.db, "get_recovery_events",
                        lambda a, fid=None: [dict(e) for e in events])
    updates = []
    monkeypatch.setattr(api.db, "update_recovery_event_fields",
                        lambda a, eid, fields, ev: updates.append((eid, fields)))
    monkeypatch.setattr(api, "resolve_connector_key", lambda a: None)
    client = TestClient(api.app)
    resp = client.post("/api/billing/sync-recoveries", headers=_auth())
    assert resp.status_code == 200
    results = resp.json()["retry_results"]
    assert {r["recovery_event_id"] for r in results} == {"re1", "re3"}
    assert all(r["fee_retry_count"] == 1 for r in results)
    assert state["pays"] == ["in_a", "in_c"]
    assert len(updates) == 2
