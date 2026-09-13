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
    monkeypatch.setattr(firebase_auth, "verify_id_token", lambda token, check_revoked=False: {"uid": "uid-123", "email": "u@example.com", "account_id": "acct-456"})
    client = TestClient(api.app)

    resp = client.get("/api/findings", headers={"Authorization": "Bearer token-123"})
    assert resp.status_code == 200
    assert captured["account_id"] == "acct-456"

    monkeypatch.setattr(firebase_auth, "verify_id_token", lambda token, check_revoked=False: {"uid": "uid-789", "email": "u2@example.com"})
    resp = client.get("/api/findings", headers={"Authorization": "Bearer token-456"})
    assert resp.status_code == 200
    assert captured["account_id"] == "uid-789"


def test_api_rejects_revoked_or_deleted_user_token(monkeypatch):
    """A signature-valid token must be re-checked against the live user record
    so tokens of revoked or deleted users are refused (AUTH-006/AUTH-012)."""
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    calls = {}

    def fake_verify(token, check_revoked=False):
        calls["check_revoked"] = check_revoked
        if check_revoked:
            raise firebase_auth.UserNotFoundError("deleted")
        return {"uid": "gone", "email": "gone@example.com"}

    monkeypatch.setattr(firebase_auth, "verify_id_token", fake_verify)
    monkeypatch.setattr(api.db, "get_all_findings", lambda account_id: [])
    client = TestClient(api.app)

    resp = client.get("/api/findings", headers={"Authorization": "Bearer deleted-user"})
    assert resp.status_code == 401
    assert calls["check_revoked"] is True


def test_sample_mode_routes_work_offline(monkeypatch):
    monkeypatch.setenv("RECOUP_SAMPLE_MODE", "1")

    def _boom(*args, **kwargs):
        raise AssertionError("Firestore should not be used in sample mode")

    monkeypatch.setattr(api.db, "get_all_findings", _boom)
    monkeypatch.setattr(api.db, "get_pending_findings", _boom)
    monkeypatch.setattr(api.db, "save_findings", _boom)
    monkeypatch.setattr(api.db, "update_finding_status", _boom)
    monkeypatch.setattr(api.db, "transition_finding_status", _boom)
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


def test_sample_header_serves_synthetic_without_env(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    client = TestClient(api.app)

    resp = client.get("/api/findings")
    assert resp.status_code == 401

    findings = client.get("/api/findings", headers={"X-Recoup-Sample": "1"})
    assert findings.status_code == 200
    assert any(item["customer_id"] == "acme" for item in findings.json())

    fee = client.post("/api/billing/charge-success-fee", headers={"X-Recoup-Sample": "1"})
    assert fee.status_code == 200
    assert fee.json()["status"] == "needs_review"

    upload = client.post(
        "/api/ingest/contract/document",
        headers={"X-Recoup-Sample": "1"},
        files={"file": ("a.txt", b"hello", "text/plain")},
    )
    assert upload.status_code == 200
    upload_payload = upload.json()
    assert upload_payload["status"] == "needs_review"
    assert "sample mode" in upload_payload["message"].lower()

    report = client.get("/report/sample")
    assert report.status_code == 200
    assert "text/html" in report.headers["content-type"]


def test_stripe_oauth_success_url_lands_on_app():
    url = api._stripe_oauth_success_url(
        {"account_id": "a1"}, {"stripe_account_id": "acct_1"}
    )
    from recoup_agent.billing.stripe_oauth import oauth_web_base_url

    assert url.startswith(oauth_web_base_url().rstrip("/") + "/app/?")
    assert "stripe_connect=success" in url


def test_stripe_oauth_error_url_lands_on_app():
    url = api._stripe_oauth_error_url("denied", account_id="a1")
    from recoup_agent.billing.stripe_oauth import oauth_web_base_url

    assert url.startswith(oauth_web_base_url().rstrip("/") + "/app/?")
    assert "stripe_connect=error" in url


def test_app_path_redirects_to_spa():
    import pytest

    if not api._web_dist.is_dir():
        pytest.skip("web/dist not built")
    client = TestClient(api.app)
    resp = client.get("/app", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/app/"
