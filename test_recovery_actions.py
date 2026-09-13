"""Governed recovery execution: transition matrix, amount immutability,
adapter gates, outcome cascade, tenant isolation, command-center links."""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from recoup_agent import api
from recoup_agent.recovery_actions import drafting, models, service
from recoup_agent.recovery_actions.adapters import (DocumentAdapter,
                                                  EmailDraftAdapter,
                                                  ManualAdapter, REGISTRY)
from recoup_agent.recovery_actions.models import (AmountTampered, NotAuthorized,
                                                  assert_amount_unchanged,
                                                  new_action)


def _finding(status="approved", amount=1200.0, fid="F-acme-202606-MIN"):
    return {"finding_id": fid, "customer_id": "acme", "customer_name": "Acme",
            "type": "unenforced_minimum", "title": "Unenforced minimum",
            "monthly_recoverable": amount, "currency": "USD",
            "status": status, "period": "2026-06", "confidence_score": 0.9,
            "clause_ref": "committed_minimum",
            "clause_text": "Minimum $1,000/mo.", "math": "1000 - 500 = 500",
            "provenance": "contract p.2", "expected_value": 1000.0,
            "actual_value": 500.0, "created_at": "2026-06-01T00:00:00+00:00"}


def _action(finding=None, **kw):
    return new_action("acct", finding or _finding(), "corrective_invoice",
                      created_by="a@b.c", **kw).to_dict()


# 1. exhaustive transition matrix
def test_transition_matrix_exhaustive():
    states = list(models.ACTION_TRANSITIONS)
    for frm in states:
        for to in states:
            a = _action()
            a["status"] = frm
            if to in models.ACTION_TRANSITIONS[frm]:
                service.transition(a, to, actor="u")
                assert a["status"] == to
                assert a["history"][-1]["to"] == to
            else:
                with pytest.raises(ValueError):
                    service.transition(a, to, actor="u")
                assert a["status"] == frm


# 2. creation gating + evidence refs
def test_new_action_requires_approved_finding_and_copies_amount():
    for status in ("approved", "invoiced", "disputed"):
        a = new_action("acct", _finding(status), "credit_request",
                       created_by="x")
        assert a.requested_value == 1200.0
        assert a.recovery_case_id == a.finding_id == "F-acme-202606-MIN"
    for status in ("open", "recovered", "rejected", "written_off"):
        with pytest.raises(ValueError):
            new_action("acct", _finding(status), "credit_request",
                       created_by="x")
    with pytest.raises(ValueError):
        new_action("acct", _finding(), "bogus_type", created_by="x")
    refs = _action()["evidence_references"]
    kinds = {r["kind"] for r in refs}
    assert {"clause", "calculation", "provenance", "period"} <= kinds


# 3. amount immutability
def test_amount_tampering_blocked_everywhere():
    a = _action()
    a["requested_value"] = 999999.0
    finding = _finding()
    with pytest.raises(AmountTampered):
        assert_amount_unchanged(a, finding)
    for target in ("pending_approval",):
        with pytest.raises(AmountTampered):
            service.transition(a, target, actor="u", finding=finding)
    a["status"] = "approved"
    with pytest.raises(AmountTampered):
        ManualAdapter().execute(a, finding, actor="u",
                                external_reference="ref")
    # draft endpoint 422 when amount text removed — covered in API test below


def test_draft_endpoint_422_and_model_fallback():
    finding = _finding()
    # model returns a wrong amount → falls back to template, source template
    class FakeModels:
        def generate_content(self, **kw):
            class R:
                text = '{"body": "Pay us $9,999.00 immediately."}'
            return R()

    class FakeClient:
        models = FakeModels()

    body, source = drafting.model_draft(finding, "credit_request",
                                        client=FakeClient())
    assert source == "template"
    assert "$1,200.00" in body
    # a good model draft is accepted and flagged model
    good = ('{"body": "Please remit $1,200.00 for 2026-06 per the agreement '
            '(expected $1,000.00, billed $500.00)."}')

    class GoodModels:
        def generate_content(self, **kw):
            class R:
                text = good
            return R()

    class GoodClient:
        models = GoodModels()

    body2, source2 = drafting.model_draft(finding, "credit_request",
                                          client=GoodClient())
    assert source2 == "model"
    assert "$1,200.00" in body2
    # model raising → template fallback, never raises
    class BadClient:
        class models:
            @staticmethod
            def generate_content(**kw):
                raise RuntimeError("boom")

    body3, source3 = drafting.model_draft(finding, "credit_request",
                                          client=BadClient())
    assert source3 == "template"


# 4. execute gates
def test_execute_requires_approved():
    finding = _finding()
    for st in ("draft", "pending_approval", "rejected"):
        a = _action(); a["status"] = st
        with pytest.raises(NotAuthorized):
            ManualAdapter().execute(a, finding, actor="u",
                                    external_reference="r")
    a = _action(); a["status"] = "sent"
    with pytest.raises(NotAuthorized):
        DocumentAdapter().execute(a, finding, actor="u")

    for adapter, kw in ((DocumentAdapter(), {}),
                        (EmailDraftAdapter(), {}),
                        (ManualAdapter(), {"external_reference": "manual-1"})):
        a = _action()
        service.submit_for_approval(a, actor="u", finding=finding)
        service.approve(a, actor="u2", finding=finding)
        action, result = service.execute(a, finding, adapter, "u3",
                                         external_reference=kw.get(
                                             "external_reference"))
        assert action["status"] == "sent"
        assert action["channel"] == adapter.name
        assert action["external_reference"]
        assert action["executed_at"] == result["executed_at"]


def test_manual_requires_reference():
    a = _action(); a["status"] = "approved"
    with pytest.raises(NotAuthorized):
        ManualAdapter().execute(a, _finding(), actor="u")


# 5. no external side effects
def test_adapters_no_side_effects_and_pdf_has_amount():
    finding = _finding()
    a = _action(); a["status"] = "approved"
    email = EmailDraftAdapter()
    artifact = email.execute(a, finding, actor="u")["artifact"]
    assert artifact["kind"] == "email_draft"
    assert artifact["subject"] and artifact["body"]
    # document adapter renders a real PDF containing the amount
    import io
    from pypdf import PdfReader
    pdf = DocumentAdapter()._render(a, finding)
    assert pdf.startswith(b"%PDF")
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "1,200.00" in text


# 6. outcome recording + cascade covered in API test (needs db layer)
def test_outcome_terminal_and_fields():
    a = _action()
    a["status"] = "sent"
    service.record_outcome(a, "resolved", "paid", "ref-1", "u")
    assert a["status"] == "resolved"
    assert a["outcome"]["result"] == "resolved"
    assert a["outcome"]["response_reference"] == "ref-1"
    with pytest.raises(ValueError):
        service.transition(a, "draft", actor="u")
    with pytest.raises(ValueError):
        service.record_outcome(a, "bogus", None, None, "u")


# ---- API-level tests with fake db ----

class FakeDb:
    """Tenant-scoped in-memory db: store[account][collection] maps."""

    def __init__(self):
        self.findings = {"acct-a": {}, "acct-b": {}}
        self.actions = {"acct-a": {}, "acct-b": {}}
        self.audit = []

    def get_finding(self, account_id, fid):
        f = self.findings.get(account_id, {}).get(fid)
        return copy.deepcopy(f) if f else None

    def get_recovery_actions(self, account_id, finding_id=None):
        out = list(self.actions.get(account_id, {}).values())
        if finding_id:
            out = [a for a in out if a.get("finding_id") == finding_id]
        return [copy.deepcopy(a) for a in out]

    def get_recovery_action(self, account_id, aid):
        a = self.actions.get(account_id, {}).get(aid)
        return copy.deepcopy(a) if a else None

    def save_recovery_action(self, account_id, action):
        self.actions.setdefault(account_id, {})[
            action["recovery_action_id"]] = copy.deepcopy(action)

    def update_recovery_action(self, account_id, action, event):
        self.save_recovery_action(account_id, action)
        self.audit.append({"event": event,
                           "recovery_action_id": action["recovery_action_id"]})

    def get_all_findings(self, account_id):
        return list(self.findings.get(account_id, {}).values())

    def get_all_contracts(self, account_id):
        return []

    def get_recovery_events(self, account_id):
        return []

    def get_audit_log(self, account_id):
        return list(self.audit)

    def get_assurance_status(self, account_id):
        return None

    def transition_finding_status(self, account_id, fid, new_status, event,
                                  fields=None):
        f = self.findings.get(account_id, {}).get(fid)
        if f is None:
            raise api.db.FindingNotFound(fid)
        try:
            from recoup_agent.recovery import assert_transition
            assert_transition(f.get("status", "open"), new_status)
        except ValueError:
            raise api.db.IllegalTransition(f.get("status", "open"), new_status)
        f["status"] = new_status
        self.audit.append({"event": event, "finding_id": fid})
        return dict(f)


@pytest.fixture
def api_env(monkeypatch):
    fake = FakeDb()
    fake.findings["acct-a"]["F-acme-202606-MIN"] = _finding()
    for name in ("get_finding", "get_recovery_action", "get_recovery_actions",
                 "save_recovery_action", "update_recovery_action",
                 "get_all_findings", "get_all_contracts", "get_recovery_events",
                 "get_audit_log", "get_assurance_status",
                 "transition_finding_status"):
        monkeypatch.setattr(api.db, name, getattr(fake, name))
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    from firebase_admin import auth as firebase_auth
    monkeypatch.setattr(
        firebase_auth, "verify_id_token",
        lambda t, **kw: {"uid": "u1", "email": "u@t.co",
                         "account_id": {"ta": "acct-a"}.get(t, "acct-b")})
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    client = TestClient(api.app)
    client.fake_db = fake
    return client


def _h(account="a"):
    return {"Authorization": f"Bearer t{account}"}


@pytest.fixture
def client_a(api_env):
    return api_env


@pytest.fixture
def client_b(api_env):
    return api_env


def _create(client):
    r = client.post("/api/findings/F-acme-202606-MIN/recovery-actions",
                    json={"action_type": "corrective_invoice"}, headers=_h("a"))
    assert r.status_code == 200, r.text
    return r.json()


def test_api_full_lifecycle(client_a):
    a = _create(client_a)
    assert a["requested_value"] == 1200.0
    assert a["status"] == "draft"
    aid = a["recovery_action_id"]
    h = _h("a")

    # draft edit ok; removing the amount → 422
    assert client_a.post(f"/api/recovery-actions/{aid}/draft",
                         json={"draft_communication": "Pay $1,200.00 now."},
                         headers=h).status_code == 200
    r = client_a.post(f"/api/recovery-actions/{aid}/draft",
                      json={"draft_communication": "Pay up."}, headers=h)
    assert r.status_code == 422

    # execute before approval → 409
    assert client_a.post(f"/api/recovery-actions/{aid}/execute",
                         json={"channel": "manual", "external_reference": "x"},
                         headers=h).status_code == 409
    assert client_a.post(f"/api/recovery-actions/{aid}/submit",
                         headers=h).status_code == 200
    assert client_a.post(f"/api/recovery-actions/{aid}/execute",
                         json={"channel": "manual", "external_reference": "x"},
                         headers=h).status_code == 409
    assert client_a.post(f"/api/recovery-actions/{aid}/approve",
                         headers=h).status_code == 200
    # preview
    r = client_a.get(f"/api/recovery-actions/{aid}/preview?channel=email_draft",
                     headers=h)
    assert r.status_code == 200 and r.json()["kind"] == "email_draft"
    r = client_a.post(f"/api/recovery-actions/{aid}/execute",
                      json={"channel": "email_draft"}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "sent" and body["channel"] == "email_draft"
    assert body["external_reference"] and body["executed_at"]

    # outcome resolved → link hint, no recovery events created
    r = client_a.post(f"/api/recovery-actions/{aid}/outcome",
                      json={"result": "resolved", "response_reference": "em-1"},
                      headers=h)
    assert r.status_code == 200
    assert "recovery-events" in r.json()["next"]
    assert r.json()["outcome"]["result"] == "resolved"


def test_api_create_requires_actionable(client_a, client_b):
    ha = _h("a")
    r = client_a.post("/api/findings/F-acme-202606-MIN/recovery-actions",
                      json={"action_type": "credit_request"}, headers=ha)
    assert r.status_code == 200
    # open finding → 409 on create
    client_a.fake_db.findings["acct-a"]["F-acme-202606-MIN"]["status"] = "open"
    r = client_a.post("/api/findings/F-acme-202606-MIN/recovery-actions",
                      json={"action_type": "credit_request"}, headers=ha)
    assert r.status_code == 409
    client_a.fake_db.findings["acct-a"]["F-acme-202606-MIN"]["status"] = "approved"
    r = client_a.get("/api/findings/F-acme-202606-MIN/recovery-actions",
                     headers=ha)
    assert len(r.json()) == 1
    aid = r.json()[0]["recovery_action_id"]
    # tenant isolation: account B sees 404 for A's action
    assert client_b.get(f"/api/recovery-actions/{aid}",
                        headers=_h("b")).status_code == 404
    assert client_b.post(f"/api/recovery-actions/{aid}/submit",
                         headers=_h("b")).status_code == 404


def test_disputed_outcome_cascades_finding(client_a):
    h = _h("a")
    a = _create(client_a)
    aid = a["recovery_action_id"]
    client_a.post(f"/api/recovery-actions/{aid}/submit", headers=h)
    client_a.post(f"/api/recovery-actions/{aid}/approve", headers=h)
    client_a.post(f"/api/recovery-actions/{aid}/execute",
                  json={"channel": "manual", "external_reference": "sent-1"},
                  headers=h)
    # disputed outcome cascades an invoiced finding to disputed
    client_a.fake_db.findings["acct-a"]["F-acme-202606-MIN"]["status"] = "invoiced"
    r = client_a.post(f"/api/recovery-actions/{aid}/outcome",
                      json={"result": "disputed", "note": "customer pushes back"},
                      headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "disputed"
    assert (client_a.fake_db.findings["acct-a"]["F-acme-202606-MIN"]["status"]
            == "disputed")


def test_command_center_recovery_actions(client_a):
    h = _h("a")
    a = _create(client_a)
    r = client_a.get("/api/command-center", headers=h)
    case = next(c for c in r.json()["cases"]
                if c["finding_id"] == "F-acme-202606-MIN")
    assert case["recovery_actions"][0]["id"] == a["recovery_action_id"]
    assert case["recommended_next_step"] == "Submit for approval"
    aid = a["recovery_action_id"]
    client_a.post(f"/api/recovery-actions/{aid}/submit", headers=h)
    case = next(c for c in client_a.get("/api/command-center",
                                        headers=h).json()["cases"]
                if c["finding_id"] == "F-acme-202606-MIN")
    assert case["recommended_next_step"] == "Approve recovery action"
