import firebase_admin.auth as firebase_auth
from fastapi.testclient import TestClient

from recoup_agent import api
from recoup_agent.reconciliation import _needs_review


def _authed_client(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(firebase_auth, "verify_id_token",
                        lambda token, **kw: {"uid": "acct1", "email": "a@b.c"})
    return TestClient(api.app)


def test_health_endpoint_and_security_headers(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    client = TestClient(api.app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert resp.json()["version"]
    assert resp.headers["strict-transport-security"].startswith("max-age=31536000")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in resp.headers["permissions-policy"]


def test_bulk_upload_rejects_over_50_files(monkeypatch):
    client = _authed_client(monkeypatch)
    files = [("files", (f"c{i}.txt", b"x", "text/plain")) for i in range(51)]
    resp = client.post("/api/ingest/bulk", files=files,
                       headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 413
    assert "Too many files" in resp.json()["detail"]


def test_bulk_upload_rejects_oversize_file(monkeypatch):
    client = _authed_client(monkeypatch)
    big = b"x" * (26 * 1024 * 1024)
    resp = client.post("/api/ingest/bulk",
                       files=[("files", ("big.txt", big, "text/plain"))],
                       headers={"Authorization": "Bearer tok"})
    assert resp.status_code == 413
    assert "25 MB" in resp.json()["detail"]


def test_needs_review_always_has_suggested_action():
    review = []
    contract = {"customer_id": "acme", "customer_name": "Acme Corp"}
    _needs_review(review, contract, "minimum_monthly_commit", "test reason")
    assert review[0]["suggested_action"]
    _needs_review(review, contract, "term_end", "x", suggested_action="Custom action.")
    assert review[1]["suggested_action"] == "Custom action."
