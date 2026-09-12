"""Validation track 8: tenant isolation and authn on all discovery/recovery
surfaces, and full account-deletion coverage of the new collections."""
import types

import firebase_admin.auth as firebase_auth
import pytest
from fastapi.testclient import TestClient

from recoup_agent import api


def _authed(monkeypatch, findings_by_acct=None, events=None):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(firebase_auth, "verify_id_token",
                        lambda token, **kw: {"uid": token, "email": f"{token}@x"})
    findings_by_acct = findings_by_acct or {}
    events = events or {}
    monkeypatch.setattr(
        api.db, "get_all_findings",
        lambda a: list(findings_by_acct.get(a, [])))
    monkeypatch.setattr(
        api.db, "get_pending_findings",
        lambda a: list(findings_by_acct.get(a, [])))
    monkeypatch.setattr(
        api.db, "get_finding",
        lambda a, fid: next((dict(f) for f in findings_by_acct.get(a, [])
                             if f["finding_id"] == fid), None))
    monkeypatch.setattr(api.db, "get_account_billing",
                        lambda _a: {"payment_method_id": "pm",
                                    "stripe_customer_id": "cus"})
    monkeypatch.setattr(
        api.db, "get_recovery_events",
        lambda a, fid=None: [e for e in events.get(a, [])
                             if fid is None or e.get("finding_id") == fid])
    return TestClient(api.app)


_A = {"Authorization": "Bearer acctA"}
_B = {"Authorization": "Bearer acctB"}


def test_cross_tenant_candidates_and_compiled(monkeypatch):
    client = _authed(monkeypatch)
    monkeypatch.setattr(api.db, "get_candidate_rights",
                        lambda a, customer_id=None:
                        [{"candidate_id": "c"}] if a == "acctA" else [])
    monkeypatch.setattr(api.db, "get_compiled_rights",
                        lambda a, customer_id=None: [])
    assert client.get("/api/rights/candidates", headers=_A).json()["candidates"]
    assert client.get("/api/rights/candidates", headers=_B).json()[
        "candidates"] == []


def test_cross_tenant_observations_and_evaluate(monkeypatch):
    client = _authed(monkeypatch)
    saved = []
    monkeypatch.setattr(api.db, "save_observation",
                        lambda a, o: saved.append((a, o)))
    monkeypatch.setattr(api.db, "get_observations",
                        lambda a, *a2, **kw:
                        [o for acc, o in saved if acc == a])
    monkeypatch.setattr(api.db, "get_compiled_rights",
                        lambda a, customer_id=None:
                        [{"spec": {}, "customer_id": "cust"}]
                        if a == "acctA" else [])
    monkeypatch.setattr(api.db, "save_findings", lambda a, fs: None)

    r = client.post("/api/observations", headers=_B,
                    json={"customer_id": "cust", "type": "monthly_uptime",
                          "period": "2026-06", "value": 99.9})
    assert r.status_code == 200
    # acctB's evaluate for the same customer sees none of acctA's compiled
    # rights — nothing evaluated, nothing persisted
    r = client.post("/api/rights/evaluate", headers=_B,
                    json={"customer_id": "cust", "period": "2026-06"})
    assert r.status_code == 200
    body = r.json()
    assert body["findings_created"] == 0
    assert body["evaluations"] == []


def test_cross_tenant_recovery_events(monkeypatch):
    findings = {"acctA": [{"finding_id": "f1", "status": "recovered",
                           "monthly_recoverable": 100.0}]}
    events = {"acctA": [{"recovery_event_id": "ev1", "finding_id": "f1",
                        "event_type": "realization", "realized_value": 100.0}]}
    client = _authed(monkeypatch, findings, events)
    mine = client.get("/api/findings/f1/recovery-events", headers=_A)
    assert len(mine.json()["events"]) == 1
    other = client.get("/api/findings/f1/recovery-events", headers=_B)
    assert other.json()["events"] == []
    r = client.post("/api/findings/f1/recovery-events", headers=_B,
                    json={"recovery_basis": "cash_payment",
                          "realized_value": 10.0})
    assert r.status_code == 404
    r = client.post("/api/findings/f1/recovery-events/ev1/reverse",
                    headers=_B, json={"reversal_amount": 5.0})
    assert r.status_code == 404


@pytest.mark.parametrize("method,path", [
    ("get", "/api/rights/candidates"),
    ("post", "/api/observations"),
    ("post", "/api/rights/evaluate"),
    ("get", "/api/findings/f1/recovery-events"),
    ("post", "/api/findings/f1/recovery-events"),
    ("post", "/api/findings/f1/recovery-events/ev1/reverse"),
    ("post", "/api/billing/charge-success-fee"),
    ("post", "/api/billing/sync-recoveries"),
    ("get", "/api/metrics"),
])
def test_unauthenticated_requests_rejected(monkeypatch, method, path):
    client = _authed(monkeypatch)
    kwargs = {"json": {}} if method == "post" else {}
    resp = getattr(client, method)(path, **kwargs)
    assert resp.status_code in (401, 403), (method, path, resp.status_code)


def test_delete_account_data_covers_new_collections(monkeypatch):
    """delete_account_data iterates every subcollection of the account root —
    the discovery/recovery collections are removed automatically."""
    deleted = []

    class FakeDoc:
        def __init__(self, name):
            self.reference = name

    class FakeColl:
        def __init__(self, name):
            self.id = name

        def stream(self):
            return [FakeDoc(f"{self.id}/d1")]

    class FakeBatch:
        def delete(self, ref):
            deleted.append(ref)

        def commit(self):
            pass

    class FakeRoot:
        def collections(self):
            return [FakeColl(n) for n in (
                "findings", "audit_log", "candidate_rights",
                "compiled_rights", "observations", "recovery_events")]

        def delete(self):
            deleted.append("root")

    class FakeClient:
        def collection(self, _n):
            return self

        def document(self, _id):
            return FakeRoot()

        def batch(self):
            return FakeBatch()

    monkeypatch.setattr(api.db, "get_client", lambda: FakeClient())
    counts = api.db.delete_account_data("acctA")
    for coll in ("candidate_rights", "compiled_rights", "observations",
                 "recovery_events"):
        assert counts.get(coll) == 1
        assert f"{coll}/d1" in deleted
    assert "root" in deleted
