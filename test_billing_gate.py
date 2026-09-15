import sys
import types

import firebase_admin.auth as firebase_auth
import pytest
from fastapi.testclient import TestClient

from recoup_agent import api
from recoup_agent.billing import recoup_billing


@pytest.fixture(autouse=True)
def _terms_accepted(monkeypatch):
    """Terms acceptance is required before any fee charge; tests that exercise
    the charging paths stub this read at the db seam."""
    record = {"version": recoup_billing.TERMS_VERSION,
              "accepted_at": "2026-09-01T00:00:00+00:00"}
    monkeypatch.setattr(recoup_billing._db, "get_terms_acceptance",
                        lambda _a: record)
    monkeypatch.setattr(api.db, "get_terms_acceptance", lambda _a: record)


def _install_fake_stripe(monkeypatch, paid_status="paid"):
    """Minimal fake stripe module covering the calls recoup_billing makes."""
    state = {"customers": {}, "invoices": {}, "items": [], "sessions": {}}
    counter = {"n": 0}

    def _next(prefix):
        counter["n"] += 1
        return f"{prefix}_{counter['n']}"

    class Customer:
        @staticmethod
        def create(email=None, metadata=None, api_key=None):
            cid = _next("cus")
            state["customers"][cid] = {"email": email, "metadata": metadata}
            return types.SimpleNamespace(id=cid)

        @staticmethod
        def modify(cid, invoice_settings=None, api_key=None):
            state["customers"].setdefault(cid, {})["invoice_settings"] = invoice_settings
            return types.SimpleNamespace(id=cid)

    class Invoice:
        @staticmethod
        def create(customer=None, **kwargs):
            iid = _next("in")
            state["invoices"][iid] = types.SimpleNamespace(
                id=iid, customer=customer, status="draft", amount_paid=0,
                hosted_invoice_url=f"https://pay.stripe.test/{iid}")
            return state["invoices"][iid]

        @staticmethod
        def finalize_invoice(iid, api_key=None):
            state["invoices"][iid].status = "open"
            return state["invoices"][iid]

        @staticmethod
        def pay(iid, api_key=None):
            state["invoices"][iid].status = paid_status
            state["invoices"][iid].amount_paid = 6400
            return state["invoices"][iid]

    class InvoiceItem:
        @staticmethod
        def create(**kwargs):
            state["items"].append(kwargs)
            return types.SimpleNamespace(id=_next("ii"))

    class SessionNS:
        @staticmethod
        def create(**kwargs):
            sid = _next("cs")
            state["sessions"][sid] = kwargs
            return types.SimpleNamespace(id=sid, url="https://checkout.stripe.test/x")

        @staticmethod
        def retrieve(sid, expand=None, api_key=None):
            return state.get("sessions", {}).get(f"obj_{sid}")

    fake = types.SimpleNamespace(
        Customer=Customer, Invoice=Invoice, InvoiceItem=InvoiceItem,
        checkout=types.SimpleNamespace(Session=SessionNS))
    monkeypatch.setitem(sys.modules, "stripe", fake)
    return fake, state


def test_billing_status_without_key(monkeypatch):
    monkeypatch.delenv("RECOUP_BILLING_STRIPE_API_KEY", raising=False)
    monkeypatch.setattr(recoup_billing._db, "get_account_billing", lambda _a: None)
    status = recoup_billing.billing_status("acct1")
    assert status["configured"] is False


def test_billing_status_no_card(monkeypatch):
    monkeypatch.setenv("RECOUP_BILLING_STRIPE_API_KEY", "sk_test_billing")
    monkeypatch.setattr(recoup_billing._db, "get_account_billing", lambda _a: None)
    status = recoup_billing.billing_status("acct1")
    assert status["configured"] is True
    assert status["card_on_file"] is False
    assert status["success_fee_pct"] == 0.20


def test_complete_setup_rejects_mismatched_customer(monkeypatch):
    monkeypatch.setenv("RECOUP_BILLING_STRIPE_API_KEY", "sk_test_billing")
    fake, state = _install_fake_stripe(monkeypatch)
    session = types.SimpleNamespace(customer="cus_other", status="complete")
    state["sessions"]["obj_cs_bad"] = session
    monkeypatch.setattr(recoup_billing._db, "get_account_billing",
                        lambda _a: {"stripe_customer_id": "cus_mine"})
    result = recoup_billing.complete_setup_session("acct1", "cs_bad")
    assert result["status"] == "error"


def test_complete_setup_success(monkeypatch):
    monkeypatch.setenv("RECOUP_BILLING_STRIPE_API_KEY", "sk_test_billing")
    fake, state = _install_fake_stripe(monkeypatch)
    saved = {}
    monkeypatch.setattr(recoup_billing._db, "get_account_billing",
                        lambda _a: saved or {"stripe_customer_id": "cus_mine"})
    monkeypatch.setattr(recoup_billing._db, "set_account_billing",
                        lambda _a, b: saved.update(b))
    pm = types.SimpleNamespace(id="pm_1", card=types.SimpleNamespace(brand="visa", last4="4242"))
    state["sessions"]["obj_cs_ok"] = types.SimpleNamespace(
        customer="cus_mine", status="complete",
        setup_intent=types.SimpleNamespace(payment_method=pm))
    result = recoup_billing.complete_setup_session("acct1", "cs_ok")
    assert result["status"] == "success"
    assert saved["payment_method_id"] == "pm_1"
    assert saved["card_brand"] == "visa"
    assert saved["card_last4"] == "4242"
    assert saved["card_on_file_at"]
    assert result["billing"]["card_on_file"] is True


def test_charge_success_fee_paid(monkeypatch):
    monkeypatch.setenv("RECOUP_BILLING_STRIPE_API_KEY", "sk_test_billing")
    _install_fake_stripe(monkeypatch)
    monkeypatch.setattr(recoup_billing._db, "get_account_billing",
                        lambda _a: {"stripe_customer_id": "cus_1", "payment_method_id": "pm_1"})
    finding = {"finding_id": "f1", "customer_name": "Acme", "period": "2026-06"}
    result = recoup_billing.charge_success_fee_for_finding("acct1", finding, 3200.0)
    assert result["status"] == "paid"
    assert result["amount"] == 640.00
    assert result["invoice_id"].startswith("in_")


def test_charge_success_fee_unbilled_without_card(monkeypatch):
    monkeypatch.setenv("RECOUP_BILLING_STRIPE_API_KEY", "sk_test_billing")
    _install_fake_stripe(monkeypatch)
    monkeypatch.setattr(recoup_billing._db, "get_account_billing", lambda _a: None)
    result = recoup_billing.charge_success_fee_for_finding("acct1", {"finding_id": "f1"}, 100.0)
    assert result["status"] == "unbilled"


def _authed_client(monkeypatch, billing_record):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setenv("RECOUP_BILLING_STRIPE_API_KEY", "sk_test_billing")
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(firebase_auth, "verify_id_token",
                        lambda token, **kw: {"uid": "acct1", "email": "a@b.c"})
    monkeypatch.setattr(api.db, "get_account_billing", lambda _a: billing_record)
    finding = {
        "finding_id": "f1", "customer_id": "acme", "customer_name": "Acme",
        "period": "2026-06", "title": "Missed minimum", "monthly_recoverable": 500.0,
        "status": "open", "confidence_score": 0.9,
        "provenance": "Section 4 minimum $500",
        "clause_text": "Customer commits to $500/mo",
        "math": "$500 - $0 = $500", "detail": "charged nothing",
    }
    monkeypatch.setattr(api.db, "get_all_findings", lambda _a: [dict(finding)])
    monkeypatch.setattr(api.db, "get_pending_findings", lambda _a: [dict(finding)])
    monkeypatch.setattr(api.db, "get_all_contracts", lambda _a: [])
    monkeypatch.setattr(api.db, "get_all_usage", lambda _a: [])
    return TestClient(api.app)


def test_findings_locked_and_report_402_without_card(monkeypatch):
    client = _authed_client(monkeypatch, None)
    resp = client.get("/api/findings", headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 200
    row = resp.json()[0]
    assert row["locked"] is True
    assert row["provenance"] == api._LOCKED_MESSAGE
    assert row["monthly_recoverable"] == 500.0

    report = client.get("/api/report", headers={"Authorization": "Bearer tok"})
    assert report.status_code == 402
    assert "payment method" in report.json()["detail"]

    pdf = client.get("/api/report.pdf", headers={"Authorization": "Bearer tok"})
    assert pdf.status_code == 402

    export = client.get("/api/findings/export", headers={"Authorization": "Bearer tok"})
    assert export.status_code == 402


def test_findings_unlocked_with_card_on_file(monkeypatch):
    client = _authed_client(monkeypatch,
                            {"stripe_customer_id": "cus_1", "payment_method_id": "pm_1"})
    resp = client.get("/api/findings", headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 200
    row = resp.json()[0]
    assert "locked" not in row
    assert row["provenance"] == "Section 4 minimum $500"

    monkeypatch.setattr(api, "_report_for_account", lambda _a: {"customers": []})
    report = client.get("/api/report", headers={"Authorization": "Bearer tok"})
    assert report.status_code == 200


def test_sample_mode_never_locked(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setenv("RECOUP_BILLING_STRIPE_API_KEY", "sk_test_billing")
    client = TestClient(api.app)
    findings = client.get("/api/findings", headers={"X-Recoup-Sample": "1"})
    assert findings.status_code == 200
    assert all("locked" not in f for f in findings.json())

    status = client.get("/api/billing/status", headers={"X-Recoup-Sample": "1"})
    assert status.status_code == 200
    assert status.json()["card_on_file"] is True


def test_sync_recoveries_paid_invoice(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setenv("RECOUP_BILLING_STRIPE_API_KEY", "sk_test_billing")
    fake, _state = _install_fake_stripe(monkeypatch)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(firebase_auth, "verify_id_token",
                        lambda token, **kw: {"uid": "acct1", "email": "a@b.c"})
    monkeypatch.setattr(api, "resolve_connector_key", lambda _a: "sk_tenant_read")
    monkeypatch.setattr(recoup_billing._db, "get_account_billing",
                        lambda _a: {"stripe_customer_id": "cus_1", "payment_method_id": "pm_1"})

    finding = {
        "finding_id": "f9", "customer_id": "acme", "customer_name": "Acme",
        "period": "2026-06", "title": "Missed minimum", "monthly_recoverable": 500.0,
        "status": "invoiced",
        "corrective_invoice": {"ref": "in_tenant1", "amount": 500.0},
    }
    monkeypatch.setattr(api.db, "get_all_findings", lambda _a: [dict(finding)])
    monkeypatch.setattr(api.db, "get_finding", lambda _a, fid: dict(finding))
    monkeypatch.setattr(api.db, "get_account_billing",
                        lambda _a: {"stripe_customer_id": "cus_1", "payment_method_id": "pm_1"})
    status_calls = []
    field_calls = []
    def _transition(a, fid, new_status, event_name, fields=None):
        status_calls.append((a, fid, new_status))
        finding["status"] = new_status
        if fields:
            finding.update(fields)
        return dict(finding)
    monkeypatch.setattr(api.db, "transition_finding_status", _transition)
    monkeypatch.setattr(api.db, "update_finding_fields",
                        lambda *a, **k: field_calls.append(a[1:]))
    monkeypatch.setattr(api.db, "get_recovery_events", lambda *a, **k: [])
    monkeypatch.setattr(api.db, "save_recovery_event", lambda *a, **k: True)
    monkeypatch.setattr(api.db, "update_recovery_event_fields",
                        lambda *a, **k: None)

    # tenant invoice retrieve → paid
    def retrieve_paid(ref, api_key=None):
        assert api_key == "sk_tenant_read"
        return {
            "id": ref, "status": "paid", "amount_paid": 50000,
            "payment_intent": "pi_9", "status_transitions": {"paid_at": 1750000000},
        }
    fake.Invoice.retrieve = staticmethod(retrieve_paid)

    client = TestClient(api.app)
    resp = client.post("/api/billing/sync-recoveries",
                       headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "success"
    assert payload["checked"] == 1
    assert payload["recovered"] == ["f9"]
    assert status_calls[0][:3] == ("acct1", "f9", "recovered")
    fee_fields = [c for c in field_calls if isinstance(c[1], dict) and "fee_charge" in c[1]]
    assert fee_fields and fee_fields[0][1]["fee_charge"]["status"] == "paid"


def test_sync_recoveries_needs_connector(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(firebase_auth, "verify_id_token",
                        lambda token, **kw: {"uid": "acct1", "email": "a@b.c"})
    monkeypatch.setattr(api, "resolve_connector_key", lambda _a: None)
    monkeypatch.setattr(api.db, "get_all_findings", lambda _a: [])
    client = TestClient(api.app)
    resp = client.post("/api/billing/sync-recoveries",
                       headers={"Authorization": "Bearer tok"})
    assert resp.json()["status"] == "needs_connector"
    assert resp.json()["checked"] == 0


def test_report_survives_legacy_finding_without_type(monkeypatch):
    """Firestore docs written before `type` was persisted must not 500 the report."""
    client = _authed_client(monkeypatch,
                            {"stripe_customer_id": "cus_1", "payment_method_id": "pm_1"})
    legacy_finding = {
        "finding_id": "f-legacy", "customer_id": "acme", "customer_name": "Acme",
        "period": "2026-06", "title": "Missed minimum", "monthly_recoverable": 500.0,
        "status": "open", "detail": "charged nothing",
    }
    monkeypatch.setattr(api.db, "get_all_findings", lambda _a: [legacy_finding])
    resp = client.get("/api/report", headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 200
    report = resp.json()
    assert len(report["customers"]) == 1
    row = report["customers"][0]["rows"][0]
    assert row["amount"] == 500.0


def test_save_findings_persists_report_fields(monkeypatch):
    saved = []

    class FakeDocRef:
        def get(self):
            return types.SimpleNamespace(exists=False)

    class FakeBatch:
        def set(self, ref, data, merge=False):
            saved.append(data)

        def commit(self):
            pass

    class FakeAccount:
        def collection(self, _name):
            return self

        def document(self, _id):
            return FakeDocRef()

    class FakeClient:
        def collection(self, _name):
            return self

        def document(self, _id):
            return FakeAccount()

        def batch(self):
            return FakeBatch()

    monkeypatch.setattr(api.db, "get_client", lambda: FakeClient())
    api.db.save_findings("acct-1", [{
        "finding_id": "f1", "customer_id": "acme", "customer_name": "Acme",
        "type": "missed_minimum", "period": "2026-06", "title": "t",
        "detail": "d", "monthly_recoverable": 500.0,
        "math": "m", "clause_text": "c", "assumption": "a",
        "provenance": "p",
    }])
    assert len(saved) == 1
    data = saved[0]
    assert data["type"] == "missed_minimum"
    assert data["math"] == "m"
    assert data["clause_text"] == "c"
    assert data["assumption"] == "a"
