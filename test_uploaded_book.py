"""Uploaded CSV books must win over the Stripe connector, and a missing
connector key must mean no provider at all."""
import types

import firebase_admin.auth as firebase_auth
from fastapi.testclient import TestClient

from recoup_agent import api, pipeline


def _contract(customer_id="acme"):
    return {
        "customer_id": customer_id, "customer_name": "Acme Corp",
        "minimum_monthly_commit": 500.0,
        "term_meta": {"minimum_monthly_commit": {"confidence": 0.95, "provenance": "S4"}},
        "clauses": {},
    }


def _usage(customer_id="acme", period="2026-06"):
    return {"customer_id": customer_id, "period": period, "units": 0}


def _invoice(customer_id="acme", period="2026-06", base=0.0):
    return {"customer_id": customer_id, "period": period, "base_charge": base,
            "line_items": [{"description": "Overage", "amount": 100.0}]}


class FakeProvider:
    def __init__(self):
        self.calls = []

    def get_usage(self, customer_id, period):
        self.calls.append(("usage", customer_id, period))
        return types.SimpleNamespace(total_units=0)

    def get_invoices(self, customer_id, period):
        self.calls.append(("invoices", customer_id, period))
        return []


def test_uploaded_book_wins_over_provider(monkeypatch):
    monkeypatch.setenv("RECOUP_BILLING_SOURCE", "stripe")
    provider = FakeProvider()
    book = ([_contract()], [_usage()], [_invoice()])
    findings, review = pipeline.compute_findings_and_review(
        "2026-06", account_id="acct1", billing_provider=provider, book=book)
    assert provider.calls == []  # uploaded data wins; connector never called
    no_provider, _ = pipeline.compute_findings_and_review(
        "2026-06", account_id="acct1", billing_provider=None, book=book)
    monkeypatch.delenv("RECOUP_BILLING_SOURCE", raising=False)
    assert [f["finding_id"] for f in findings] == [f["finding_id"] for f in no_provider]


def test_provider_used_when_book_lacks_customer(monkeypatch):
    monkeypatch.setenv("RECOUP_BILLING_SOURCE", "stripe")
    provider = FakeProvider()
    book = ([_contract()], [], [])  # nothing uploaded for this customer
    pipeline.compute_findings_and_review(
        "2026-06", account_id="acct1", billing_provider=provider, book=book)
    assert provider.calls == [("usage", "acme", "2026-06"),
                              ("invoices", "acme", "2026-06")]


def test_no_provider_when_connector_key_missing(monkeypatch):
    monkeypatch.setenv("RECOUP_BILLING_SOURCE", "stripe")
    monkeypatch.setattr(pipeline, "resolve_connector_key", lambda _a: None)
    assert pipeline._selected_billing_provider("acct1") is None


def test_get_contracts_endpoint(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(firebase_auth, "verify_id_token",
                        lambda token, **kw: {"uid": "acct1", "email": "a@b.c"})
    monkeypatch.setattr(api.db, "get_all_contracts",
                        lambda _a: [{"customer_id": "acme", "customer_name": "Acme Corp"}])
    client = TestClient(api.app)
    resp = client.get("/api/contracts", headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 200
    assert resp.json()["contracts"][0]["customer_id"] == "acme"

    sample = client.get("/api/contracts", headers={"X-Recoup-Sample": "1"})
    assert sample.status_code == 200
    assert any(c["customer_id"] == "acme" for c in sample.json()["contracts"])
