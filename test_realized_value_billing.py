"""Realized-recovered-value billing: fee arithmetic on events, reversal credit
notes, idempotency, eligibility gating, tenant isolation."""
import inspect
import sys
import types

import firebase_admin.auth as firebase_auth
import pytest
from fastapi.testclient import TestClient

from recoup_agent import api, success_fee
from recoup_agent.billing import realized_value as rv
from recoup_agent.billing import recoup_billing


def _install_fake_stripe(monkeypatch, paid_status="paid", fail=False):
    state = {"invoice_creates": [], "items": [], "credit_notes": []}
    counter = {"n": 0}

    def _next(prefix):
        counter["n"] += 1
        return f"{prefix}_{counter['n']}"

    class Invoice:
        @staticmethod
        def create(customer=None, **kwargs):
            state["invoice_creates"].append(kwargs)
            if fail:
                raise RuntimeError("stripe boom")
            iid = _next("in")
            return types.SimpleNamespace(
                id=iid, status="draft",
                hosted_invoice_url=f"https://pay.stripe.test/{iid}")

        @staticmethod
        def finalize_invoice(iid, api_key=None):
            return types.SimpleNamespace(id=iid, status="open")

        @staticmethod
        def pay(iid, api_key=None):
            return types.SimpleNamespace(
                id=iid, status=paid_status,
                hosted_invoice_url=f"https://pay.stripe.test/{iid}")

    class InvoiceItem:
        @staticmethod
        def create(**kwargs):
            state["items"].append(kwargs)
            return types.SimpleNamespace(id=_next("ii"))

    class CreditNote:
        @staticmethod
        def create(**kwargs):
            state["credit_notes"].append(kwargs)
            return types.SimpleNamespace(id=_next("cn"))

    fake = types.SimpleNamespace(Invoice=Invoice, InvoiceItem=InvoiceItem,
                                 CreditNote=CreditNote)
    monkeypatch.setitem(sys.modules, "stripe", fake)
    return fake, state


class _Store:
    """In-memory findings + recovery_events for one or more accounts."""

    def __init__(self):
        self.findings = {}   # (account, finding_id) -> dict
        self.events = {}     # (account, event_id) -> dict

    def add_finding(self, account, fid, status="approved", **kw):
        self.findings[(account, fid)] = {
            "finding_id": fid, "customer_id": "acme", "customer_name": "Acme",
            "period": "2026-06", "monthly_recoverable": 10000.0,
            "status": status, **kw}
        return self.findings[(account, fid)]


def _wire(monkeypatch, store, *, billing_card=True, paid_status="paid",
          stripe_fail=False):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setenv("RECOUP_BILLING_STRIPE_API_KEY", "sk_test_billing")
    _fake, state = _install_fake_stripe(monkeypatch, paid_status=paid_status,
                                        fail=stripe_fail)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(firebase_auth, "verify_id_token",
                        lambda token, **kw: {"uid": token, "email": f"{token}@b.c"})

    billing = ({"stripe_customer_id": "cus_1", "payment_method_id": "pm_1"}
               if billing_card else None)
    monkeypatch.setattr(api.db, "get_account_billing", lambda _a: billing)
    monkeypatch.setattr(recoup_billing._db, "get_account_billing",
                        lambda _a: billing)

    def get_finding(a, fid):
        f = store.findings.get((a, fid))
        return dict(f) if f else None
    monkeypatch.setattr(api.db, "get_finding", get_finding)
    monkeypatch.setattr(api.db, "get_all_findings",
                        lambda a: [dict(f) for (acc, _), f in store.findings.items()
                                   if acc == a])
    monkeypatch.setattr(api.db, "get_pending_findings", lambda a: [])

    def update_status(a, fid, status, _ev, fields=None):
        store.findings[(a, fid)]["status"] = status
        if fields:
            store.findings[(a, fid)].update(fields)
    monkeypatch.setattr(api.db, "update_finding_status", update_status)
    monkeypatch.setattr(api.db, "transition_finding_status",
                        lambda a, fid, status, ev, fields=None:
                        update_status(a, fid, status, ev, fields) or
                        dict(store.findings[(a, fid)]))
    monkeypatch.setattr(api.db, "update_finding_fields",
                        lambda a, fid, fields, _ev:
                        store.findings[(a, fid)].update(fields))

    def get_events(a, fid=None):
        return [dict(e) for (acc, _), e in store.events.items()
                if acc == a and (fid is None or e["finding_id"] == fid)]
    monkeypatch.setattr(api.db, "get_recovery_events", get_events)

    def save_event(a, e):
        key = (a, e["recovery_event_id"])
        if key in store.events:
            return False
        store.events[key] = dict(e)
        return True
    monkeypatch.setattr(api.db, "save_recovery_event", save_event)
    monkeypatch.setattr(api.db, "update_recovery_event_fields",
                        lambda a, eid, fields, _ev:
                        store.events[(a, eid)].update(fields))
    return TestClient(api.app), state


def _auth(uid="acct1"):
    return {"Authorization": f"Bearer {uid}"}


def test_event_fee_arithmetic_10k(monkeypatch):
    store = _Store()
    store.add_finding("acct1", "f1")
    client, state = _wire(monkeypatch, store)
    r = client.post("/api/findings/f1/recovery-events", headers=_auth(),
                    json={"recovery_basis": "cash_payment",
                          "realized_value": 10000.0,
                          "external_reference": "pmt-1"})
    assert r.status_code == 200
    event = r.json()
    assert event["fee_amount"] == 2000.0
    assert event["fee_status"] == "paid"
    assert event["fee_charge"]["amount"] == 2000.0
    assert len(state["invoice_creates"]) == 1
    assert state["items"][0]["amount"] == 200000
    finding = store.findings[("acct1", "f1")]
    assert finding["status"] == "recovered"
    assert finding["recovered_amount"] == 10000.0
    assert finding["fee_charge"]["status"] == "paid"


def test_two_partials_distinct_idempotency(monkeypatch):
    store = _Store()
    store.add_finding("acct1", "f1")
    client, state = _wire(monkeypatch, store)
    r1 = client.post("/api/findings/f1/recovery-events", headers=_auth(),
                     json={"recovery_basis": "cash_payment",
                           "realized_value": 5000.0,
                           "external_reference": "pmt-a"})
    r2 = client.post("/api/findings/f1/recovery-events", headers=_auth(),
                     json={"recovery_basis": "cash_payment",
                           "realized_value": 3000.0,
                           "external_reference": "pmt-b"})
    assert r1.json()["fee_amount"] == 1000.0
    assert r2.json()["fee_amount"] == 600.0
    keys = [c["idempotency_key"] for c in state["invoice_creates"]]
    assert len(keys) == 2 and keys[0] != keys[1]
    assert store.findings[("acct1", "f1")]["recovered_amount"] == 8000.0


def test_duplicate_external_reference_409_one_charge(monkeypatch):
    store = _Store()
    store.add_finding("acct1", "f1")
    client, state = _wire(monkeypatch, store)
    body = {"recovery_basis": "cash_payment", "realized_value": 4000.0,
            "external_reference": "pmt-dup"}
    first = client.post("/api/findings/f1/recovery-events",
                        headers=_auth(), json=body)
    assert first.status_code == 200
    dup = client.post("/api/findings/f1/recovery-events",
                      headers=_auth(), json=body)
    assert dup.status_code == 409
    assert dup.json()["detail"]["status"] == "duplicate"
    assert dup.json()["detail"]["recovery_event_id"] == \
        first.json()["recovery_event_id"]
    assert len(state["invoice_creates"]) == 1


def test_reversal_credits_paid_fee_once(monkeypatch):
    store = _Store()
    store.add_finding("acct1", "f1")
    client, state = _wire(monkeypatch, store)
    r = client.post("/api/findings/f1/recovery-events", headers=_auth(),
                    json={"recovery_basis": "cash_payment",
                          "realized_value": 10000.0,
                          "external_reference": "pmt-1"})
    event_id = r.json()["recovery_event_id"]

    rev = client.post(
        f"/api/findings/f1/recovery-events/{event_id}/reverse",
        headers=_auth(),
        json={"reversal_amount": 4000.0, "reversal_reference": "rf-1"})
    assert rev.status_code == 200
    body = rev.json()
    assert body["event_type"] == "reversal"
    assert body["fee_amount"] == -800.0
    assert body["fee_status"] == "adjusted"
    assert body["net_realized"] == 6000.0
    assert len(state["credit_notes"]) == 1
    assert state["credit_notes"][0]["amount"] == 80000
    assert state["credit_notes"][0]["reason"] == "order_change"
    # original event never mutated beyond fee bookkeeping
    original = store.events[("acct1", event_id)]
    assert original["realized_value"] == 10000.0
    assert original["event_type"] == "realization"
    assert store.findings[("acct1", "f1")]["recovered_amount"] == 6000.0
    assert store.findings[("acct1", "f1")]["status"] == "recovered"

    # duplicate reversal reference -> 409, no second credit note
    dup = client.post(
        f"/api/findings/f1/recovery-events/{event_id}/reverse",
        headers=_auth(),
        json={"reversal_amount": 4000.0, "reversal_reference": "rf-1"})
    assert dup.status_code == 409
    assert len(state["credit_notes"]) == 1


def test_over_reversal_422_and_full_reversal_marks_finding(monkeypatch):
    store = _Store()
    store.add_finding("acct1", "f1")
    client, _ = _wire(monkeypatch, store)
    r = client.post("/api/findings/f1/recovery-events", headers=_auth(),
                    json={"recovery_basis": "refund", "realized_value": 1000.0})
    eid = r.json()["recovery_event_id"]
    over = client.post(f"/api/findings/f1/recovery-events/{eid}/reverse",
                       headers=_auth(), json={"reversal_amount": 1500.0})
    assert over.status_code == 422
    full = client.post(f"/api/findings/f1/recovery-events/{eid}/reverse",
                       headers=_auth(),
                       json={"reversal_amount": 1000.0, "reversal_reference": "x"})
    assert full.status_code == 200
    f = store.findings[("acct1", "f1")]
    assert f["recovered_amount"] == 0.0
    assert f["status"] == "recovered"
    assert f["metadata"]["fully_reversed"] is True


def test_all_bases_fee_20pct(monkeypatch):
    for i, basis in enumerate(rv.RECOVERY_BASES):
        store = _Store()
        store.add_finding("acct1", f"f{i}")
        client, state = _wire(monkeypatch, store)
        r = client.post(f"/api/findings/f{i}/recovery-events", headers=_auth(),
                        json={"recovery_basis": basis, "realized_value": 2500.0})
        assert r.status_code == 200, basis
        assert r.json()["fee_amount"] == 500.0
        assert len(state["invoice_creates"]) == 1


def test_invalid_basis_and_value_422(monkeypatch):
    store = _Store()
    store.add_finding("acct1", "f1")
    client, _ = _wire(monkeypatch, store)
    for bad in ({"recovery_basis": "magic", "realized_value": 100.0},
                {"recovery_basis": "cash_payment", "realized_value": 0},
                {"recovery_basis": "cash_payment", "realized_value": -50.0}):
        r = client.post("/api/findings/f1/recovery-events",
                        headers=_auth(), json=bad)
        assert r.status_code == 422, bad


@pytest.mark.parametrize("status", ["open", "rejected", "written_off"])
def test_unapproved_finding_409_no_charge(monkeypatch, status):
    store = _Store()
    store.add_finding("acct1", "f1", status=status)
    client, state = _wire(monkeypatch, store)
    r = client.post("/api/findings/f1/recovery-events", headers=_auth(),
                    json={"recovery_basis": "cash_payment",
                          "realized_value": 1000.0})
    assert r.status_code == 409
    assert "approved" in r.json()["detail"]
    assert not state["invoice_creates"]
    assert not store.events


def test_stripe_failure_error_then_retry_charges_once(monkeypatch):
    store = _Store()
    store.add_finding("acct1", "f1")
    client, _ = _wire(monkeypatch, store, stripe_fail=True)
    r = client.post("/api/findings/f1/recovery-events", headers=_auth(),
                    json={"recovery_basis": "cash_payment",
                          "realized_value": 2000.0,
                          "external_reference": "pmt-e"})
    assert r.status_code == 200
    assert r.json()["fee_status"] == "error"
    assert store.findings[("acct1", "f1")]["status"] == "recovered"

    # retry via charge-success-fee once Stripe is healthy again
    _fake, state2 = _install_fake_stripe(monkeypatch)
    res = client.post("/api/billing/charge-success-fee", headers=_auth())
    assert res.status_code == 200
    charged = res.json()["billing"]["charged"]
    assert len(charged) == 1
    assert charged[0]["status"] == "paid"
    assert charged[0]["amount"] == 400.0
    assert len(state2["invoice_creates"]) == 1
    # a second run finds nothing unbilled
    res2 = client.post("/api/billing/charge-success-fee", headers=_auth())
    assert res2.json()["billing"]["charged"] == []
    assert len(state2["invoice_creates"]) == 1


def test_new_realization_has_no_fee_params_and_payload_fee_ignored(monkeypatch):
    params = inspect.signature(rv.new_realization).parameters
    for p in ("fee_amount", "fee_status", "feeable_value", "fee_percentage",
              "fee_charge"):
        assert p not in params

    store = _Store()
    store.add_finding("acct1", "f1")
    client, _ = _wire(monkeypatch, store)
    r = client.post("/api/findings/f1/recovery-events", headers=_auth(),
                    json={"recovery_basis": "rebate", "realized_value": 1000.0,
                          "fee_amount": 1.0, "fee_status": "paid"})
    assert r.status_code == 200
    assert r.json()["fee_amount"] == 200.0


def test_metrics_from_events(monkeypatch):
    findings = [{"finding_id": "f1", "monthly_recoverable": 10000.0,
                 "status": "recovered", "recovered_amount": 9999.0,
                 "recovered_at": "2026-07-01T00:00:00+00:00"}]
    events = [
        {"finding_id": "f1", "event_type": "realization",
         "recovery_basis": "cash_payment", "realized_value": 8000.0},
        {"finding_id": "f1", "event_type": "realization",
         "recovery_basis": "contractual_credit", "realized_value": 1000.0},
        {"finding_id": "f1", "event_type": "reversal",
         "recovery_basis": "cash_payment", "reversal_amount": 2000.0},
    ]
    m = success_fee.compute_metrics(findings, events=events)
    assert m["recovered_to_date"] == 7000.0
    assert m["success_fee_to_date"] == 1400.0
    assert m["realized_value_by_basis"] == {
        "cash_payment": 6000.0, "contractual_credit": 1000.0}
    # without events, legacy field drives metrics
    m2 = success_fee.compute_metrics(findings)
    assert m2["recovered_to_date"] == 9999.0
    assert "realized_value_by_basis" not in m2


def test_tenant_isolation_events(monkeypatch):
    store = _Store()
    store.add_finding("acct1", "f1")
    client, _ = _wire(monkeypatch, store)
    client.post("/api/findings/f1/recovery-events", headers=_auth("acct1"),
                json={"recovery_basis": "cash_payment", "realized_value": 500.0})
    mine = client.get("/api/findings/f1/recovery-events", headers=_auth("acct1"))
    assert len(mine.json()["events"]) == 1
    other = client.get("/api/findings/f1/recovery-events", headers=_auth("acct2"))
    assert other.status_code == 404
    assert other.json() == {"detail": "Finding not found."}
    # acct2 cannot create events against acct1's finding
    r = client.post("/api/findings/f1/recovery-events", headers=_auth("acct2"),
                    json={"recovery_basis": "cash_payment", "realized_value": 5.0})
    assert r.status_code == 404


def test_legacy_recovered_endpoint_contract(monkeypatch):
    store = _Store()
    store.add_finding("acct1", "f1", status="invoiced")
    client, state = _wire(monkeypatch, store)
    r = client.post("/api/findings/f1/recovered", headers=_auth(),
                    json={"paid_amount": 3200.0, "payment_ref": "pmt-9"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "recovered"
    assert body["finding_id"] == "f1"
    assert body["recovered_amount"] == 3200.0
    assert body["payment"]["ref"] == "pmt-9"
    assert body["fee_charge"]["status"] == "paid"
    assert body["fee_charge"]["amount"] == 640.0
    # the realization used cash_payment + payment_ref as external_reference
    evs = list(store.events.values())
    assert len(evs) == 1
    assert evs[0]["recovery_basis"] == "cash_payment"
    assert evs[0]["external_reference"] == "pmt-9"


def test_legacy_finding_synthesized_event_billed_once(monkeypatch):
    """A finding recovered before the event model gets one synthesized
    other_verified_value event and is billed exactly once."""
    store = _Store()
    store.add_finding("acct1", "fold", status="recovered",
                      recovered_amount=5000.0)
    client, state = _wire(monkeypatch, store)
    r1 = client.post("/api/billing/charge-success-fee", headers=_auth())
    charged = r1.json()["billing"]["charged"]
    assert len(charged) == 1 and charged[0]["status"] == "paid"
    assert charged[0]["amount"] == 1000.0
    evs = list(store.events.values())
    assert len(evs) == 1
    assert evs[0]["recovery_basis"] == "other_verified_value"
    assert evs[0]["external_reference"] == "legacy:fold"
    r2 = client.post("/api/billing/charge-success-fee", headers=_auth())
    assert r2.json()["billing"]["charged"] == []
    assert len(state["invoice_creates"]) == 1
