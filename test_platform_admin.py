import firebase_admin.auth as firebase_auth
import pytest
from fastapi.testclient import TestClient

from recoup_agent import api


@pytest.fixture
def admin_state(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setenv("RECOUP_OPERATOR_EMAILS", "Ops@Example.com")
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    api._platform_gate_down_until = 0.0
    api._tenant_registry_down_until = 0.0

    state = {
        "settings": {"signup_enabled": True, "invited_emails": []},
        "tenants": {},
        "admin_audit": [],
        "findings": {},
        "events": {},
        "audit": {},
        "contracts": {},
        "actions": {},
        "assurance_status": {},
        "assurance_events": {},
        "billing": {},
        "deleted": [],
    }
    users = {
        "operator": {"uid": "ops-uid", "email": "ops@example.com"},
        "tenant": {"uid": "tenant-uid", "email": "tenant@example.com"},
        "invited": {"uid": "invited-uid", "email": "invited@example.com"},
        "existing": {"uid": "existing-uid", "email": "existing@example.com"},
    }

    def get_settings():
        return dict(state["settings"])

    def set_settings(settings):
        state["settings"] = {
            "signup_enabled": bool(settings.get("signup_enabled", True)),
            "invited_emails": list(settings.get("invited_emails") or []),
        }
        return dict(state["settings"])

    def touch(account_id, email):
        state["tenants"].setdefault(account_id, {
            "account_id": account_id,
            "email": email,
            "first_seen": "2026-09-01T00:00:00+00:00",
            "demo": False,
        })["last_seen"] = "2026-09-14T00:00:00+00:00"

    monkeypatch.setattr(api.db, "get_platform_settings", get_settings)
    monkeypatch.setattr(api.db, "set_platform_settings", set_settings)
    monkeypatch.setattr(api.db, "touch_tenant", touch)
    monkeypatch.setattr(api.db, "get_tenant",
                        lambda account_id: state["tenants"].get(account_id))
    monkeypatch.setattr(api.db, "list_tenants",
                        lambda: list(state["tenants"].values()))

    def set_demo(account_id, demo, **fields):
        tenant = state["tenants"].setdefault(
            account_id, {"account_id": account_id, "email": None})
        tenant.update({"demo": bool(demo), **fields})
        return dict(tenant)

    monkeypatch.setattr(api.db, "set_tenant_demo", set_demo)
    monkeypatch.setattr(api.db, "append_admin_audit",
                        lambda entry: state["admin_audit"].append(entry) or entry)
    monkeypatch.setattr(api.db, "list_admin_audit",
                        lambda limit=100: state["admin_audit"][-limit:])
    monkeypatch.setattr(api.db, "get_all_findings",
                        lambda account_id: state["findings"].get(account_id, []))
    monkeypatch.setattr(api.db, "get_recovery_events",
                        lambda account_id: state["events"].get(account_id, []))
    monkeypatch.setattr(api.db, "get_audit_log",
                        lambda account_id: state["audit"].get(account_id, []))
    monkeypatch.setattr(api.db, "get_all_contracts",
                        lambda account_id: state["contracts"].get(account_id, []))
    monkeypatch.setattr(api.db, "get_recovery_actions",
                        lambda account_id: state["actions"].get(account_id, []))
    monkeypatch.setattr(api.db, "get_assurance_status",
                        lambda account_id: state["assurance_status"].get(account_id))
    monkeypatch.setattr(api.db, "get_assurance_events",
                        lambda account_id, limit=50: state["assurance_events"].get(account_id, [])[:limit])
    monkeypatch.setattr(api.db, "get_account_billing",
                        lambda account_id: state["billing"].get(account_id))

    def delete_account(account_id):
        state["deleted"].append(account_id)
        return {"findings": 2, "audit_log": 3}

    monkeypatch.setattr(api.db, "delete_account_data", delete_account)
    monkeypatch.setattr(firebase_auth, "verify_id_token",
                        lambda token, check_revoked=False: users[token])
    return state, TestClient(api.app)


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_operator_allowlist_and_sample_mode(admin_state):
    _state, client = admin_state
    assert client.get("/api/admin/me", headers=auth("tenant")).json() == {
        "operator": False}
    assert client.get("/api/admin/me", headers=auth("operator")).json() == {
        "operator": True}

    admin_calls = [
        ("GET", "/api/admin/settings"),
        ("PUT", "/api/admin/settings"),
        ("GET", "/api/admin/tenants"),
        ("GET", "/api/admin/tenants/tenant-uid"),
        ("POST", "/api/admin/tenants/tenant-uid/demo"),
        ("POST", "/api/admin/tenants/tenant-uid/reset"),
        ("GET", "/api/admin/audit"),
    ]
    for method, path in admin_calls:
        response = client.request(method, path, headers=auth("tenant"),
                                  json={})
        assert response.status_code == 403, (method, path, response.text)
        response = client.request(method, path,
                                  headers={"X-Recoup-Sample": "1"}, json={})
        assert response.status_code == 403, (method, path, response.text)

    assert client.get("/api/admin/me",
                      headers={"X-Recoup-Sample": "1"}).json() == {
        "operator": False}


def test_signup_gate_matrix(admin_state):
    state, client = admin_state
    state["settings"] = {"signup_enabled": False, "invited_emails": []}

    response = client.get("/api/findings", headers=auth("tenant"))
    assert response.status_code == 403
    assert "invite-only" in response.json()["detail"]

    state["settings"]["invited_emails"] = ["invited@example.com"]
    assert client.get("/api/findings", headers=auth("invited")).status_code == 200

    state["tenants"]["existing-uid"] = {"account_id": "existing-uid"}
    assert client.get("/api/findings", headers=auth("existing")).status_code == 200

    assert client.get("/api/findings", headers=auth("operator")).status_code == 200

    state["settings"]["signup_enabled"] = True
    assert client.get("/api/findings", headers=auth("tenant")).status_code == 200


def test_signup_gate_fails_open_on_firestore_error(admin_state, monkeypatch):
    _state, client = admin_state
    monkeypatch.setattr(api.db, "get_platform_settings",
                        lambda: (_ for _ in ()).throw(RuntimeError("firestore down")))
    monkeypatch.setattr(api.db, "touch_tenant",
                        lambda *args: (_ for _ in ()).throw(RuntimeError("down")))
    response = client.get("/api/findings", headers=auth("tenant"))
    assert response.status_code == 200


def test_tenant_list_enriches_and_isolates_errors(admin_state, monkeypatch):
    state, client = admin_state
    state["tenants"] = {
        "acct-a": {"account_id": "acct-a", "email": "a@example.com"},
        "acct-b": {"account_id": "acct-b", "email": "b@example.com"},
    }
    state["findings"]["acct-a"] = [
        {"finding_id": "f-open", "status": "open", "monthly_recoverable": 100},
        {"finding_id": "f-approved", "status": "approved",
         "monthly_recoverable": 200},
    ]
    state["events"]["acct-a"] = [{
        "finding_id": "f-approved", "event_type": "realization",
        "realized_value": 50, "fee_amount": 10,
        "recovery_basis": "cash_payment",
        "realized_at": "2026-09-14T00:00:00+00:00",
    }]
    state["billing"]["acct-a"] = {"payment_method_id": "pm_123"}
    state["assurance_status"]["acct-a"] = {
        "last_evaluated_at": "2026-09-14T01:00:00+00:00"}

    original = api.db.get_all_findings

    def flaky_findings(account_id):
        if account_id == "acct-b":
            raise RuntimeError("broken tenant")
        return original(account_id)

    monkeypatch.setattr(api.db, "get_all_findings", flaky_findings)
    response = client.get("/api/admin/tenants", headers=auth("operator"))

    assert response.status_code == 200
    rows = {row["account_id"]: row for row in response.json()}
    assert rows["acct-a"]["findings_by_status"] == {"open": 1, "approved": 1}
    assert rows["acct-a"]["potential_value"] == 300
    assert rows["acct-a"]["realized_value"] == 50
    assert rows["acct-a"]["fee_billed"] == 10
    assert rows["acct-a"]["billing"] == {"card_on_file": True}
    assert rows["acct-a"]["assurance_last_evaluated"] == \
        "2026-09-14T01:00:00+00:00"
    assert "broken tenant" in rows["acct-b"]["error"]


def test_tenant_detail_demo_and_reset(admin_state):
    state, client = admin_state
    state["tenants"]["demo-a"] = {
        "account_id": "demo-a", "email": "demo@example.com", "demo": True}
    state["audit"]["demo-a"] = [{"event": "approved", "ts": "2026-09-01"}]
    state["assurance_events"]["demo-a"] = [
        {"event_id": "e1", "trigger": "new_usage",
         "received_at": "2026-09-02"}]

    detail = client.get("/api/admin/tenants/demo-a", headers=auth("operator"))
    assert detail.status_code == 200
    assert detail.json()["audit_log"] == state["audit"]["demo-a"]
    assert detail.json()["assurance_events"] == state["assurance_events"]["demo-a"]

    bad_confirm = client.post("/api/admin/tenants/demo-a/reset",
                              headers=auth("operator"),
                              json={"confirm_account_id": "other"})
    assert bad_confirm.status_code == 400

    state["tenants"]["prod-a"] = {"account_id": "prod-a", "demo": False}
    non_demo = client.post("/api/admin/tenants/prod-a/reset",
                           headers=auth("operator"),
                           json={"confirm_account_id": "prod-a"})
    assert non_demo.status_code == 409

    reset = client.post("/api/admin/tenants/demo-a/reset",
                        headers=auth("operator"),
                        json={"confirm_account_id": "demo-a"})
    assert reset.status_code == 200
    assert state["deleted"] == ["demo-a"]
    assert reset.json()["deleted"] == {"findings": 2, "audit_log": 3}
    assert state["tenants"]["demo-a"]["demo"] is True
    assert state["tenants"]["demo-a"]["reset_at"]
    assert state["admin_audit"][-1]["action"] == "tenant_reset"


def test_settings_put_validates_normalizes_and_audits(admin_state):
    state, client = admin_state
    bad = client.put("/api/admin/settings", headers=auth("operator"),
                     json={"signup_enabled": False,
                           "invited_emails": ["not-an-email"]})
    assert bad.status_code == 400

    good = client.put(
        "/api/admin/settings", headers=auth("operator"),
        json={"signup_enabled": False,
              "invited_emails": [" A@Example.COM ", "a@example.com",
                                  "B@Example.com"]})
    assert good.status_code == 200
    assert good.json() == {
        "signup_enabled": False,
        "invited_emails": ["a@example.com", "b@example.com"],
    }
    assert state["settings"] == good.json()
    assert state["admin_audit"][-1]["action"] == "settings_updated"
    assert state["admin_audit"][-1]["operator_email"] == "ops@example.com"
