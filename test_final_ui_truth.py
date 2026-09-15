import firebase_admin.auth as firebase_auth
from fastapi.testclient import TestClient

from recoup_agent import api


def _auth_client(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(
        firebase_auth, "verify_id_token",
        lambda token, check_revoked=False: {
            "uid": "uid-1",
            "email": "owner@example.com",
            "account_id": "acct-1",
        },
    )
    return TestClient(api.app)


def test_contract_confirmation_persists_and_audits(monkeypatch):
    client = _auth_client(monkeypatch)
    calls = []

    def fake_confirm(account_id, customer_id, actor):
        calls.append((account_id, customer_id, actor))
        return {
            "customer_id": customer_id,
            "confirmed": True,
            "confirmed_by": actor,
            "confirmed_at": "2026-06-20T00:00:00+00:00",
        }

    monkeypatch.setattr(
        api.db, "get_all_contracts",
        lambda account_id: [{"customer_id": "acme"}])
    monkeypatch.setattr(api.db, "confirm_contract", fake_confirm)
    resp = client.post("/api/contracts/acme/confirm",
                       headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"
    assert resp.json()["contract"]["confirmed"] is True
    assert calls == [("acct-1", "acme", "owner@example.com")]


def test_contract_confirmation_404_and_sample_not_persisted(monkeypatch):
    client = _auth_client(monkeypatch)
    monkeypatch.setattr(api.db, "get_all_contracts", lambda account_id: [])
    monkeypatch.setattr(api.db, "confirm_contract", lambda *args: None)
    resp = client.post("/api/contracts/missing/confirm",
                       headers={"Authorization": "Bearer token"})
    assert resp.status_code == 404

    sample = client.post("/api/contracts/acme/confirm",
                         headers={"X-Recoup-Sample": "1"})
    assert sample.status_code == 200
    assert sample.json()["status"] == "not_persisted"


def test_assurance_status_reports_emitted_triggers(monkeypatch):
    client = _auth_client(monkeypatch)
    monkeypatch.setattr(
        api.db, "get_all_contracts", lambda account_id: [])
    monkeypatch.setattr(
        api.db, "get_all_invoices", lambda account_id: [])
    monkeypatch.setattr(
        api.db, "get_all_usage", lambda account_id: [])
    monkeypatch.setattr(
        api.db, "get_assurance_events", lambda account_id, limit=50: [])
    monkeypatch.setattr(
        api.db, "get_all_findings", lambda account_id: [])
    monkeypatch.setattr(
        api.db, "get_assurance_status", lambda account_id: {})
    monkeypatch.setattr(
        api.db, "set_assurance_status", lambda account_id, status: None)
    monkeypatch.setattr(
        api.db, "get_recovery_events", lambda account_id, finding_id=None: [])

    live = client.get("/api/assurance/status",
                      headers={"Authorization": "Bearer token"})
    assert live.status_code == 200
    triggers = live.json()["triggers_monitored"]
    assert "new_payment" not in triggers
    assert "new_invoice" in triggers
    assert "new_agreement" in triggers
    assert "pricing_change" in triggers
    assert "contract_renewal" in triggers

    sample = client.get("/api/assurance/status",
                        headers={"X-Recoup-Sample": "1"})
    assert sample.status_code == 200
    assert sample.json()["triggers_monitored"] == triggers
