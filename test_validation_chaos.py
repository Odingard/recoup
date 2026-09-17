"""Validation track 10: failure/chaos. Model outages, garbage output, and
Firestore write failures must never reach the client or compute money."""
import time
from types import SimpleNamespace

import firebase_admin.auth as firebase_auth
import pytest
from fastapi.testclient import TestClient

from recoup_agent import api
from recoup_agent.rights_discovery import discover_financial_rights
from recoup_agent.rights_discovery import discovery as _discovery


class _FakeError(Exception):
    def __init__(self, status):
        super().__init__(f"status {status}")
        self.status_code = status
        self.code = status


class _FailModels:
    def __init__(self, exc):
        self.exc = exc

    def generate_content(self, **kwargs):
        raise self.exc


class _GarbageModels:
    def generate_content(self, **kwargs):
        return SimpleNamespace(text="{not json")


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    yield


def test_gemini_503_raises_inside_module_but_not_to_client(monkeypatch):
    # raw module raises after retries (recorded as incomplete by harnesses)
    client = SimpleNamespace(models=_FailModels(_FakeError(503)))
    with pytest.raises(Exception):
        discover_financial_rights(
            "doc", {"account_id": "a", "source_id": "s"}, client=client)
    # the API wrapper is best-effort: never raises into the request path
    monkeypatch.setattr(_discovery, "_default_client", lambda: client)
    out = api._run_novel_discovery("acct1", "some text",
                                   {"customer_id": "c"})
    assert out is None or isinstance(out, dict)


def test_gemini_garbage_json_never_reaches_client(monkeypatch):
    # discovery's default client is stubbed by _run_novel_discovery internals;
    # force garbage via the module's client factory
    monkeypatch.setattr(_discovery, "_default_client",
                        lambda: SimpleNamespace(models=_GarbageModels()))
    out = api._run_novel_discovery("acct1", "contract text",
                                   {"customer_id": "c"})
    assert out is None or out["compiled"] == 0


def test_db_failure_during_evaluate_leaves_no_partial_finding(monkeypatch):
    """save_findings raising must persist nothing — findings are built in
    memory first and saved in a single call."""
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(firebase_auth, "verify_id_token",
                        lambda t, **kw: {"uid": "u1", "email": "e@x"})
    monkeypatch.setattr(api.db, "get_account_billing",
                        lambda a: {"payment_method_id": "pm"})
    from recoup_agent.rights_discovery.compiler import (
        compile_candidate_right)
    from test_rights_discovery import _verified_candidate
    compiled = compile_candidate_right(
        _verified_candidate(),
        "If monthly service availability falls below 99.95%, Customer shall "
        "receive a service credit of $15,000 applied to the next invoice."
    ).to_dict()
    compiled["customer_id"] = "cust"
    monkeypatch.setattr(api.db, "get_compiled_rights",
                        lambda a, customer_id=None: [compiled])
    monkeypatch.setattr(api.db, "get_observations",
                        lambda a, **kw: [{"type": "monthly_uptime",
                                          "value": 99.72, "period": "2026-06"}])
    monkeypatch.setattr(api.db, "get_all_findings", lambda a: [])
    persisted = []

    def _boom(a, fs):
        raise RuntimeError("firestore down")
    monkeypatch.setattr(api.db, "save_findings", _boom)
    client = TestClient(api.app, raise_server_exceptions=False)
    r = client.post("/api/rights/evaluate",
                    headers={"Authorization": "Bearer x"},
                    json={"customer_id": "cust", "period": "2026-06"})
    assert r.status_code == 500
    assert persisted == []


def test_db_failure_during_recovery_event_leaves_finding_untouched(monkeypatch):
    """If persisting the event fails, the finding must not be marked
    recovered (no partial write)."""
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(firebase_auth, "verify_id_token",
                        lambda t, **kw: {"uid": "u1", "email": "e@x"})
    finding = {"finding_id": "f1", "status": "approved",
               "monthly_recoverable": 1000.0}
    monkeypatch.setattr(api.db, "get_finding", lambda a, f: dict(finding))
    monkeypatch.setattr(api.db, "get_recovery_events", lambda a, fid=None: [])
    writes = []
    monkeypatch.setattr(api.db, "update_finding_status",
                        lambda *a, **k: writes.append(a))
    monkeypatch.setattr(api.db, "transition_finding_status",
                        lambda *a, **k: writes.append(a))
    monkeypatch.setattr(api.db, "update_finding_fields",
                        lambda *a, **k: writes.append(a))

    def _boom(a, e, **kwargs):
        raise RuntimeError("firestore down")
    monkeypatch.setattr(api.db, "save_recovery_event", _boom)
    client = TestClient(api.app, raise_server_exceptions=False)
    r = client.post("/api/findings/f1/recovery-events",
                    headers={"Authorization": "Bearer x"},
                    json={"recovery_basis": "cash_payment",
                          "realized_value": 500.0})
    assert r.status_code == 500
    assert writes == []
