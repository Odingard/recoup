from __future__ import annotations

import firebase_admin.auth as firebase_auth
import pytest
from fastapi.testclient import TestClient

from recoup_agent import api
from recoup_agent import db as db_module


def _auth_client(monkeypatch, account_id="acct-1"):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(
        firebase_auth, "verify_id_token",
        lambda token: {"uid": "uid-1", "email": "u@example.com", "account_id": account_id},
    )
    return TestClient(api.app)


def test_delete_rejects_bad_confirm(monkeypatch):
    client = _auth_client(monkeypatch)
    res = client.request("DELETE", "/api/account/data", json={"confirm": "nope"},
                         headers={"Authorization": "Bearer t"})
    assert res.status_code == 400
    res = client.request("DELETE", "/api/account/data", json={},
                         headers={"Authorization": "Bearer t"})
    assert res.status_code == 400


def test_delete_sample_mode_needs_review(monkeypatch):
    monkeypatch.setenv("RECOUP_SAMPLE_MODE", "1")
    res = TestClient(api.app).request("DELETE", "/api/account/data", json={"confirm": "DELETE"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "needs_review"
    assert "sample mode" in payload["message"].lower()


def test_delete_success(monkeypatch):
    client = _auth_client(monkeypatch)
    calls = {}
    def fake_delete(acct):
        calls["db"] = acct
        return {"findings": 3, "audit_log": 5}

    def fake_secret(acct):
        calls["secret"] = acct
        return True

    monkeypatch.setattr(api.db, "delete_account_data", fake_delete)
    monkeypatch.setattr(api, "delete_connector_key", fake_secret)
    res = client.request("DELETE", "/api/account/data", json={"confirm": "DELETE"},
                         headers={"Authorization": "Bearer t"})
    assert res.status_code == 200
    payload = res.json()
    assert payload["status"] == "deleted"
    assert payload["account_id"] == "acct-1"
    assert payload["deleted"] == {"findings": 3, "audit_log": 5}
    assert payload["connector_key_deleted"] is True
    assert calls == {"db": "acct-1", "secret": "acct-1"}


def test_delete_account_data_batching():
    """Fake Firestore: 850 docs across two collections -> batched deletes <=400."""

    class FakeDoc:
        def __init__(self, ref):
            self.reference = ref

    class FakeBatch:
        def __init__(self, sink):
            self.sink = sink
            self.ops = []

        def delete(self, ref):
            self.ops.append(ref)

        def commit(self):
            self.sink.append(len(self.ops))
            self.ops = []

    class FakeCollection:
        def __init__(self, cid, n):
            self.id = cid
            self._n = n

        def stream(self):
            return iter(FakeDoc(f"{self.id}-{i}") for i in range(self._n))

    class FakeRoot:
        def __init__(self):
            self.deleted = False

        def collections(self):
            return [FakeCollection("findings", 850), FakeCollection("usage", 2)]

        def delete(self):
            self.deleted = True

    class FakeClient:
        def __init__(self):
            self.batch_sizes = []
            self.root = FakeRoot()

        def collection(self, name):
            return self

        def document(self, account_id):
            return self.root

        def batch(self):
            return FakeBatch(self.batch_sizes)

    fake = FakeClient()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(db_module, "get_client", lambda: fake)
    try:
        counts = db_module.delete_account_data("acct-9")
    finally:
        monkeypatch.undo()
    assert counts == {"findings": 850, "usage": 2}
    assert fake.root.deleted
    assert fake.batch_sizes == [400, 400, 50, 2]
    assert all(size <= 400 for size in fake.batch_sizes)


def test_delete_connector_key_missing_returns_false(monkeypatch):
    from recoup_agent.billing import connector_keys
    monkeypatch.setattr(connector_keys, "_client_instance", lambda: None)
    assert connector_keys.delete_connector_key("acct-1") is False
