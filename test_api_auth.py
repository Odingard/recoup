import firebase_admin.auth as firebase_auth
from fastapi.testclient import TestClient

from recoup_agent import api


def test_api_rejects_missing_and_invalid_token(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    client = TestClient(api.app)

    resp = client.get("/api/findings")
    assert resp.status_code == 401

    resp = client.get("/api/findings", headers={"Authorization": "Bearer not-a-token"})
    assert resp.status_code == 401


def test_api_uses_firebase_claim_account_id_then_uid(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)

    captured = {}

    def fake_get_all_findings(account_id):
        captured["account_id"] = account_id
        return []

    monkeypatch.setattr(api.db, "get_all_findings", fake_get_all_findings)
    monkeypatch.setattr(firebase_auth, "verify_id_token", lambda token: {"uid": "uid-123", "email": "u@example.com", "account_id": "acct-456"})
    client = TestClient(api.app)

    resp = client.get("/api/findings", headers={"Authorization": "Bearer token-123"})
    assert resp.status_code == 200
    assert captured["account_id"] == "acct-456"

    monkeypatch.setattr(firebase_auth, "verify_id_token", lambda token: {"uid": "uid-789", "email": "u2@example.com"})
    resp = client.get("/api/findings", headers={"Authorization": "Bearer token-456"})
    assert resp.status_code == 200
    assert captured["account_id"] == "uid-789"


def test_sample_mode_routes_work_offline(monkeypatch):
    monkeypatch.setenv("RECOUP_SAMPLE_MODE", "1")

    def _boom(*args, **kwargs):
        raise AssertionError("Firestore should not be used in sample mode")

    monkeypatch.setattr(api.db, "get_all_findings", _boom)
    monkeypatch.setattr(api.db, "get_pending_findings", _boom)
    monkeypatch.setattr(api.db, "save_findings", _boom)
    monkeypatch.setattr(api.db, "update_finding_status", _boom)
    monkeypatch.setattr(api.db, "save_usage", _boom)
    monkeypatch.setattr(api.db, "save_invoice", _boom)
    monkeypatch.setattr(api.db, "save_contract", _boom)

    client = TestClient(api.app)

    findings = client.get("/api/findings")
    assert findings.status_code == 200
    payload = findings.json()
    assert any(item["customer_id"] == "acme" for item in payload)

    reconcile = client.post("/api/reconcile", params={"period": "2026-06"})
    assert reconcile.status_code == 200
    assert reconcile.json()["findings_found"] == 4
