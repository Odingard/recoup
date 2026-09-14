import firebase_admin.auth as firebase_auth
from fastapi.testclient import TestClient

from recoup_agent import api, ingestion_doc


def _auth_client(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(
        firebase_auth,
        "verify_id_token",
        lambda token, check_revoked=False: {
            "uid": "uid-1",
            "email": "owner@example.com",
            "account_id": "acct-1",
        },
    )
    return TestClient(api.app)


def test_unreadable_pdf_provider_error_returns_friendly_422(monkeypatch):
    client = _auth_client(monkeypatch)
    monkeypatch.setattr(api, "_pdf_has_text_layer", lambda path: (True, None, 1))

    class BadModels:
        def generate_content(self, **kwargs):
            raise RuntimeError("Gemini INVALID_ARGUMENT: corrupt provider payload")

    class BadClient:
        models = BadModels()

    monkeypatch.setattr(ingestion_doc.genai, "Client", lambda: BadClient())

    response = client.post(
        "/api/ingest/contract/document",
        headers={"Authorization": "Bearer token"},
        files={"file": ("bad.pdf", b"%PDF-1.4 garbage", "application/pdf")},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == ingestion_doc.UNREADABLE_DOCUMENT_MESSAGE
    assert "Gemini" not in response.text
    assert "INVALID_ARGUMENT" not in response.text
