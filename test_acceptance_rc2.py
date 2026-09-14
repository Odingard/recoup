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


def test_finding_child_routes_are_tenant_scoped(monkeypatch):
    class TenantDb:
        findings = {"acct-a": {"F-A": {"finding_id": "F-A", "customer_id": "acme"}}}

        def get_finding(self, account_id, finding_id):
            return self.findings.get(account_id, {}).get(finding_id)

        def transition_finding_status(self, account_id, finding_id, *args, **kwargs):
            finding = self.get_finding(account_id, finding_id)
            if finding is None:
                raise api.db.FindingNotFound(finding_id)
            return finding

        def get_recovery_events(self, account_id, finding_id=None):
            return []

        def get_recovery_actions(self, account_id, finding_id=None):
            return []

        def get_audit_log(self, account_id, finding_id=None):
            return []

        def get_outcome_record(self, account_id, finding_id):
            return None

    fake = TenantDb()
    for name in ("get_finding", "transition_finding_status",
                 "get_recovery_events", "get_recovery_actions",
                 "get_audit_log", "get_outcome_record"):
        monkeypatch.setattr(api.db, name, getattr(fake, name))
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(
        firebase_auth,
        "verify_id_token",
        lambda token, check_revoked=False: {
            "uid": "uid-1",
            "email": "owner@example.com",
            "account_id": {"tenant-a": "acct-a", "tenant-b": "acct-b"}[token],
        },
    )
    client = TestClient(api.app)
    headers = {"Authorization": "Bearer tenant-b"}
    routes = [
        ("post", "/api/findings/F-A/approve", None),
        ("post", "/api/findings/F-A/reject", {"status": "rejected", "reason": "x"}),
        ("post", "/api/findings/F-A/invoiced", {
            "invoice_ref": "in_1", "invoice_amount": 100.0}),
        ("post", "/api/findings/F-A/recovery-events", {
            "recovery_basis": "cash_payment", "realized_value": 100.0}),
        ("post", "/api/findings/F-A/recovery-events/E-1/reverse", {
            "reversal_amount": 50.0}),
        ("get", "/api/findings/F-A/recovery-events", None),
        ("post", "/api/findings/F-A/recovered", {"paid_amount": 100.0}),
        ("post", "/api/findings/F-A/disputed", {"reason": "x"}),
        ("post", "/api/findings/F-A/written-off", {"reason": "x"}),
        ("post", "/api/findings/F-A/recovery-actions", {
            "action_type": "corrective_invoice", "draft_mode": "template"}),
        ("get", "/api/findings/F-A/recovery-actions", None),
        ("get", "/api/recovery-cases/F-A/ledger", None),
        ("get", "/api/recovery-cases/F-A/outcome", None),
    ]
    for method, path, body in routes:
        response = (client.get(path, headers=headers) if method == "get"
                    else client.post(path, json=body, headers=headers))
        assert response.status_code == 404, (method, path, response.text)
        assert response.json() == {"detail": "Finding not found."}


def test_actor_falls_back_to_uid_and_unknown():
    assert api._actor({"uid": "uid-1"}) == "uid-1"
    assert api._actor({"email": "owner@example.com", "uid": "uid-1"}) == "owner@example.com"
    assert api._actor({}) == "unknown"


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
