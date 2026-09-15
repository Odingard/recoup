"""Continuous assurance: stable finding ids, scoped idempotent re-evaluation,
state preservation, fail-closed ambiguity, no external actions."""
import copy

import pytest
from fastapi.testclient import TestClient

from recoup_agent import api, assurance
from recoup_agent.reconciliation import reconcile


class FakeDb:
    """In-memory stand-in for the db functions assurance uses."""

    def __init__(self):
        self.contracts = {}
        self.usage = {}
        self.invoices = {}
        self.findings = {}
        self.events = {}
        self.audit = []
        self.status = {}
        self.compiled_rights = []
        self.observations = []

    # --- reads ---
    def get_all_contracts(self, account_id):
        return [copy.deepcopy(c) for c in self.contracts.values()]

    def get_all_usage(self, account_id):
        return [copy.deepcopy(u) for u in self.usage.values()]

    def get_all_invoices(self, account_id):
        return [copy.deepcopy(i) for i in self.invoices.values()]

    def get_all_findings(self, account_id):
        return [copy.deepcopy(f) for f in self.findings.values()]

    def get_compiled_rights(self, account_id, customer_id=None):
        return list(self.compiled_rights)

    def get_observations(self, account_id, customer_id=None, period=None):
        return list(self.observations)

    # --- writes ---
    def save_contract(self, account_id, payload):
        self.contracts[payload["customer_id"]] = dict(payload)

    def save_invoice(self, account_id, payload):
        self.invoices[(payload["customer_id"], payload["period"])] = dict(payload)

    def save_usage(self, account_id, payload):
        self.usage[(payload["customer_id"], payload["period"])] = dict(payload)

    def save_findings(self, account_id, findings):
        for f in findings:
            fid = f["finding_id"]
            existing = self.findings.get(fid)
            doc = dict(f)
            if existing is not None:
                doc["status"] = existing.get("status", "open")
                doc["created_at"] = existing.get("created_at", f.get("created_at"))
            self.findings[fid] = doc

    def transition_finding_status(self, account_id, finding_id, new_status,
                                  event, fields=None):
        finding = self.findings[finding_id]
        finding["status"] = new_status
        if fields:
            finding.update(fields)
        self.audit.append({"event": event, "finding_id": finding_id,
                           **(fields or {})})
        return dict(finding)

    def assurance_event_exists(self, account_id, event_id):
        return event_id in self.events

    def save_assurance_event(self, account_id, event):
        self.events[event["event_id"]] = dict(event)

    def get_assurance_events(self, account_id, limit=50):
        docs = sorted(self.events.values(),
                      key=lambda e: e.get("received_at") or "", reverse=True)
        return docs[:limit]

    def append_assurance_audit(self, account_id, entry):
        self.audit.append(dict(entry))

    def get_assurance_status(self, account_id):
        return dict(self.status) if self.status else None

    def set_assurance_status(self, account_id, status):
        self.status.update(status)


@pytest.fixture
def fake_db(monkeypatch):
    fake = FakeDb()
    for name in ("get_all_contracts", "get_all_usage", "get_all_invoices",
                 "get_all_findings", "get_compiled_rights", "get_observations",
                 "save_contract", "save_invoice", "save_usage", "save_findings",
                 "transition_finding_status", "assurance_event_exists", "save_assurance_event",
                 "get_assurance_events", "append_assurance_audit",
                 "get_assurance_status", "set_assurance_status"):
        monkeypatch.setattr(assurance.db, name, getattr(fake, name))
        monkeypatch.setattr(api.db, name, getattr(fake, name))
    monkeypatch.delenv("RECOUP_BILLING_SOURCE", raising=False)
    return fake


def _contract(cid, minimum=1000.0):
    return {"customer_id": cid, "customer_name": f"{cid} Co",
            "committed_minimum_monthly": minimum,
            "clauses": {"committed_minimum": f"Minimum ${minimum}/mo."}}


def _usage(cid, period, units=0):
    return {"customer_id": cid, "period": period, "units": units}


def _invoice(cid, period, base=500.0):
    return {"customer_id": cid, "period": period, "base_charge": base,
            "overage_charge": 0.0}


def _event(trigger="new_invoice", cid="B", period="2026-06",
           source="ingest/invoice", payload=None):
    return assurance.make_event("acct", trigger, cid, period, source,
                                payload if payload is not None else {})


def test_finding_ids_stable_across_periods():
    contract = _contract("acme")
    inv = _invoice("acme", "2026-06")
    usage = _usage("acme", "2026-06")
    june = reconcile(contract, usage, {**inv, "period": "2026-06"}, "2026-06")
    july = reconcile(contract, usage, {**inv, "period": "2026-07"}, "2026-07")
    assert june and july
    assert june[0]["finding_id"] != july[0]["finding_id"]
    assert "202606" in june[0]["finding_id"]
    assert "202607" in july[0]["finding_id"]
    again = reconcile(contract, usage, inv, "2026-06")
    assert [f["finding_id"] for f in again] == [f["finding_id"] for f in june]


def test_event_idempotent(fake_db):
    fake_db.save_contract("acct", _contract("B"))
    fake_db.save_usage("acct", _usage("B", "2026-06"))
    fake_db.save_invoice("acct", _invoice("B", "2026-06"))
    payload = _invoice("B", "2026-06")
    ev = _event(payload=payload)
    first = assurance.evaluate_event("acct", ev)
    assert first["status"] == "evaluated"
    n_findings = len(fake_db.findings)
    second = assurance.evaluate_event("acct", ev)
    assert second["status"] == "duplicate"
    assert len(fake_db.findings) == n_findings
    assert len(fake_db.events) == 1


def test_scoped_evaluation(fake_db, monkeypatch):
    for cid in ("A", "B", "C"):
        fake_db.save_contract("acct", _contract(cid))
        fake_db.save_usage("acct", _usage(cid, "2026-06"))
        fake_db.save_invoice("acct", _invoice(cid, "2026-06"))
    calls = []
    real = assurance.compute_findings_and_review

    def spy(period, **kw):
        calls.append(kw.get("customer_ids"))
        return real(period, **kw)

    monkeypatch.setattr(assurance, "compute_findings_and_review", spy)
    out = assurance.evaluate_event("acct", _event(cid="B", payload=_invoice("B", "2026-06")))
    assert out["status"] == "evaluated"
    assert calls == [{"B"}]
    assert {f["customer_id"] for f in fake_db.findings.values()} == {"B"}


def test_state_preserved(fake_db):
    fake_db.save_contract("acct", _contract("B"))
    fake_db.save_usage("acct", _usage("B", "2026-06"))
    fake_db.save_invoice("acct", _invoice("B", "2026-06", base=500.0))
    out = assurance.evaluate_event("acct", _event(cid="B", payload=_invoice("B", "2026-06")))
    assert out["status"] == "evaluated"
    fid = next(iter(fake_db.findings))
    fake_db.findings[fid]["status"] = "approved"
    fake_db.findings[fid]["created_at"] = "2026-01-01T00:00:00"
    other = fid + "-x"
    fake_db.findings[other] = {**fake_db.findings[fid],
                             "finding_id": other, "status": "recovered"}
    # re-ingest a changed invoice for the same scope
    changed = _invoice("B", "2026-06", base=400.0)
    fake_db.save_invoice("acct", changed)
    ev = _event(cid="B", payload=changed)
    assert assurance.evaluate_event("acct", ev)["status"] == "evaluated"
    assert fake_db.findings[fid]["status"] == "approved"
    assert fake_db.findings[fid]["created_at"] == "2026-01-01T00:00:00"
    assert fake_db.findings[fid]["monthly_recoverable"] == 600.0
    assert fake_db.findings[other]["status"] == "recovered"


def test_changed_same_period_invoice_reevaluates_and_identical_is_duplicate(fake_db):
    fake_db.save_contract("acct", _contract("B"))
    fake_db.save_usage("acct", _usage("B", "2026-06"))
    user = {"account_id": "acct", "email": "u@example.com"}

    first = api.ingest_invoice(api.InvoicePayload(**_invoice("B", "2026-06", 500.0)), user)
    assert [e["trigger"] for e in first["assurance"]["events"]] == [
        "new_invoice", "new_billing_period"]
    fid = next(iter(fake_db.findings))
    fake_db.findings[fid]["status"] = "approved"

    changed = _invoice("B", "2026-06", 400.0)
    changed["invoice_id"] = "in_corrected"
    changed["uploaded_at"] = "later"
    second = api.ingest_invoice(api.InvoicePayload(**changed), user)
    events = second["assurance"]["events"]
    assert [e["trigger"] for e in events] == ["new_invoice"]
    assert events[0]["status"] == "evaluated"
    assert fake_db.findings[fid]["status"] == "approved"
    assert fake_db.findings[fid]["actual_value"] == 400.0
    assert fake_db.findings[fid]["monthly_recoverable"] == 600.0

    identical = api.ingest_invoice(api.InvoicePayload(**changed), user)
    assert identical["assurance"]["events"] == []
    assert len(fake_db.events) == 3

    metadata_only = {**changed, "invoice_id": "in_new", "uploaded_at": "newer"}
    assert assurance.classify_invoice_event(changed, metadata_only) == []


def _seed_complete_assurance_book(fake_db, finding=None, *, include_usage=True):
    fake_db.save_contract("acct", _contract("X", minimum=1000.0))
    fake_db.save_invoice("acct", _invoice("X", "2026-06", base=1000.0))
    if include_usage:
        fake_db.save_usage("acct", _usage("X", "2026-06", units=0))
    if finding is not None:
        fake_db.findings[finding["finding_id"]] = dict(finding)


def _evaluate_usage_event(fake_db):
    event = _event(trigger="new_usage", cid="X", period="2026-06",
                   payload=_usage("X", "2026-06", units=0))
    return assurance.evaluate_event("acct", event)


def test_withdraws_open_stale_finding_after_complete_evaluation(fake_db):
    finding = {"finding_id": "F-X-202606-MIN", "customer_id": "X",
               "period": "2026-06", "type": "unenforced_minimum",
               "status": "open", "monthly_recoverable": 1000.0}
    _seed_complete_assurance_book(fake_db, finding)
    result = _evaluate_usage_event(fake_db)
    stored = fake_db.findings[finding["finding_id"]]
    assert stored["status"] == "rejected"
    assert stored["withdrawal_reason"] == assurance.STALE_WITHDRAWAL_REASON
    assert any(a["event"] == "assurance_withdrawn_stale"
               and a["finding_id"] == finding["finding_id"]
               for a in fake_db.audit)
    assert result["findings_withdrawn"] == [finding["finding_id"]]


def test_does_not_withdraw_approved_stale_finding(fake_db):
    finding = {"finding_id": "F-X-202606-MIN", "customer_id": "X",
               "period": "2026-06", "type": "unenforced_minimum",
               "status": "approved", "monthly_recoverable": 1000.0}
    _seed_complete_assurance_book(fake_db, finding)
    result = _evaluate_usage_event(fake_db)
    assert fake_db.findings[finding["finding_id"]]["status"] == "approved"
    assert result["findings_withdrawn"] == []


def test_does_not_withdraw_when_period_is_incomplete(fake_db):
    finding = {"finding_id": "F-X-202606-MIN", "customer_id": "X",
               "period": "2026-06", "type": "unenforced_minimum",
               "status": "open", "monthly_recoverable": 1000.0}
    _seed_complete_assurance_book(fake_db, finding, include_usage=False)
    result = _evaluate_usage_event(fake_db)
    assert fake_db.findings[finding["finding_id"]]["status"] == "open"
    assert result["findings_withdrawn"] == []


def test_does_not_withdraw_novel_right_finding(fake_db):
    finding = {"finding_id": "F-X-202606-N-XYZ", "customer_id": "X",
               "period": "2026-06", "type": "novel:xyz", "status": "open",
               "monthly_recoverable": 1000.0}
    _seed_complete_assurance_book(fake_db, finding)
    result = _evaluate_usage_event(fake_db)
    assert fake_db.findings[finding["finding_id"]]["status"] == "open"
    assert result["findings_withdrawn"] == []


def test_ambiguous_fails_to_review(fake_db):
    out = assurance.evaluate_event("acct", _event(cid="ghost"))
    assert out["status"] == "needs_review"
    assert not fake_db.findings
    ev = fake_db.events[out["event_id"]]
    assert ev["status"] == "needs_review"


def test_contract_amendment_reevaluates_all_customer_periods(fake_db, monkeypatch):
    for cid in ("B", "C"):
        fake_db.save_contract("acct", _contract(cid))
    for p in ("2026-05", "2026-06", "2026-07"):
        fake_db.save_usage("acct", _usage("B", p))
        fake_db.save_invoice("acct", _invoice("B", p))
    fake_db.save_usage("acct", _usage("C", "2026-06"))
    fake_db.save_invoice("acct", _invoice("C", "2026-06"))
    calls = []
    real = assurance.compute_findings_and_review

    def spy(period, **kw):
        calls.append((period, kw.get("customer_ids")))
        return real(period, **kw)

    monkeypatch.setattr(assurance, "compute_findings_and_review", spy)
    ev = _event(trigger="pricing_change", cid="B", period=None,
                source="ingest/contract", payload=_contract("B", minimum=1500.0))
    out = assurance.evaluate_event("acct", ev)
    assert out["status"] == "evaluated"
    assert sorted(c[0] for c in calls) == ["2026-05", "2026-06", "2026-07"]
    assert all(c[1] == {"B"} for c in calls)


def test_classify_contract_event():
    incoming = _contract("B")
    assert assurance.classify_contract_event(None, incoming) == "new_agreement"
    same = _contract("B")
    assert assurance.classify_contract_event(same, incoming) == ""
    reordered = {
        **same,
        "contract_id": "new-doc-id",
        "uploaded_at": "later",
        "term_meta": {"committed_minimum_monthly": {"provenance": "different quote"}},
        "discounts": [
            {"name": "B", "type": "amount", "value": 20, "expires": "2027-01-01"},
            {"name": "A", "type": "percent", "value": 0.1, "starts": "2026-01-01"},
        ],
    }
    same["discounts"] = list(reversed(reordered["discounts"]))
    assert assurance.classify_contract_event(same, reordered) == ""
    assert assurance.make_event(
        "acct", "agreement_amendment", "B", None, "test", same
    ).event_id == assurance.make_event(
        "acct", "agreement_amendment", "B", None, "test", reordered
    ).event_id
    pricing = {**same, "committed_minimum_monthly": 1500.0}
    assert assurance.classify_contract_event(same, pricing) == "pricing_change"
    amended_effective = {**same, "escalator_effective_date": "2027-01-01"}
    assert assurance.classify_contract_event(same, amended_effective) == "pricing_change"
    renewed = {**same, "term_end": "2030-01-01"}
    old = {**same, "term_end": "2027-01-01"}
    assert assurance.classify_contract_event(old, renewed) == "contract_renewal"
    expired = {**same, "term_end": "2020-01-01"}
    assert assurance.classify_contract_event(same, expired) == "term_expiration"


def test_confirmation_timestamps_change_contract_event_id():
    first = {**_contract("B"), "confirmed_at": "2026-09-15T03:00:00+00:00"}
    second = {**first, "confirmed_at": "2026-09-15T03:01:00+00:00"}
    assert assurance.make_event(
        "acct", "agreement_amendment", "B", None, "test", first
    ).event_id != assurance.make_event(
        "acct", "agreement_amendment", "B", None, "test", second
    ).event_id


def test_confirmed_contract_re_evaluates_after_second_confirmation(fake_db):
    contract = _contract("B")
    fake_db.save_contract("acct", contract)
    fake_db.save_usage("acct", _usage("B", "2026-06"))
    fake_db.save_invoice("acct", _invoice("B", "2026-06"))
    first = assurance.make_event(
        "acct", "agreement_amendment", "B", None, "confirm", {
            **contract, "confirmed_at": "2026-09-15T03:00:00+00:00"})
    second = assurance.make_event(
        "acct", "agreement_amendment", "B", None, "confirm", {
            **contract, "confirmed_at": "2026-09-15T03:01:00+00:00"})
    assert assurance.evaluate_event("acct", first)["status"] == "evaluated"
    assert assurance.evaluate_event("acct", second)["status"] != "duplicate"


def test_identical_contract_ingest_does_not_duplicate_event(fake_db):
    original = _contract("B")
    first = api._save_contract_if_needed("acct", original)
    assert [e["trigger"] for e in first] == ["new_agreement"]
    assert len(fake_db.events) == 1

    identical = {
        **original,
        "contract_id": "different-document-id",
        "created_at": "later",
        "term_meta": {"committed_minimum_monthly": {"provenance": "other quote"}},
    }
    assert api._save_contract_if_needed("acct", identical) == []
    assert len(fake_db.events) == 1


def test_no_external_action(fake_db, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("external action must not be called by assurance")

    monkeypatch.setattr(api.recoup_billing, "charge_success_fee_for_event", boom)
    monkeypatch.setattr(api.recoup_billing, "create_success_fee_invoice", boom)
    monkeypatch.setattr(api.db, "transition_finding_status", boom)
    monkeypatch.setattr(api.db, "update_finding_status", boom)
    fake_db.save_contract("acct", _contract("B"))
    fake_db.save_usage("acct", _usage("B", "2026-06"))
    fake_db.save_invoice("acct", _invoice("B", "2026-06"))
    out = assurance.evaluate_event("acct", _event(cid="B", payload=_invoice("B", "2026-06")))
    assert out["status"] == "evaluated"


def test_status_endpoint(fake_db, monkeypatch):
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    from firebase_admin import auth as firebase_auth
    monkeypatch.setattr(firebase_auth, "verify_id_token",
                        lambda t, **kw: {"uid": "u1", "account_id": "acct"})
    client = TestClient(api.app)
    resp = client.get("/api/assurance/status", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    body = resp.json()
    for key in ("last_evaluated_at", "last_trigger", "next_evaluation",
                "sources_monitored", "open_discrepancies", "needs_review",
                "events_total", "events_needs_review", "recent_events"):
        assert key in body
    assert body["next_evaluation"] == "on next event"


def test_status_endpoint_sample_mode(monkeypatch):
    monkeypatch.setenv("RECOUP_SAMPLE_MODE", "1")
    client = TestClient(api.app)
    resp = client.get("/api/assurance/status",
                      headers={"X-Recoup-Sample": "1"})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "sample"


# --- Reopen of system-withdrawn findings (db.save_findings) ---------------

class _Snap:
    def __init__(self, data):
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class _FakeColl:
    def __init__(self, docs, name):
        self._docs = docs
        self._name = name
        self._seq = 0

    def document(self, doc_id=None):
        if doc_id is None:
            self._seq += 1
            doc_id = f"{self._name}-{self._seq}"
        return _FakeDocRef(self._docs, doc_id)


class _FakeDocRef:
    def __init__(self, docs, doc_id):
        self._docs = docs
        self.id = doc_id

    def get(self, transaction=None):
        return _Snap(self._docs.get(self.id))


class _FakeRootDoc:
    def __init__(self):
        self._cols = {}

    def collection(self, name):
        return self._cols.setdefault(name, _FakeColl({}, name))


class _FakeBatch:
    def __init__(self):
        self._ops = []

    def set(self, ref, data, merge=False):
        self._ops.append((ref, data, merge))

    def update(self, ref, data):
        self._ops.append((ref, data, True))

    def commit(self):
        for ref, data, merge in self._ops:
            if merge:
                ref._docs.setdefault(ref.id, {}).update(data)
            else:
                ref._docs[ref.id] = dict(data)
        self._ops = []


class _FakeFirestore:
    def __init__(self):
        self._accounts = {}

    def collection(self, name):
        assert name == "accounts"
        return self

    def document(self, account_id):
        return self._accounts.setdefault(account_id, _FakeRootDoc())

    def batch(self):
        return _FakeBatch()


def _fake_findings_db(monkeypatch, stored):
    from recoup_agent import db as db_module
    fake = _FakeFirestore()
    root = fake.document("acct")
    for fid, doc in stored.items():
        root.collection("findings")._docs[fid] = dict(doc)
    monkeypatch.setattr(db_module, "get_client", lambda: fake)
    return root


_FRESH = {"finding_id": "F-1", "customer_id": "X", "period": "2026-06",
          "type": "unenforced_minimum", "monthly_recoverable": 2500.0}


def test_system_withdrawn_finding_reopens_on_redetect(monkeypatch):
    from recoup_agent import db as db_module
    root = _fake_findings_db(monkeypatch, {
        "F-1": {"status": "rejected",
                "withdrawal_reason": db_module.STALE_WITHDRAWAL_REASON,
                "withdrawn_by": "system",
                "withdrawn_at": "2026-09-15T00:00:00+00:00",
                "created_at": "2026-09-15T00:00:00+00:00"}})
    db_module.save_findings("acct", [dict(_FRESH)])
    saved = root.collection("findings")._docs["F-1"]
    assert saved["status"] == "open"
    assert saved["withdrawal_reason"] is None
    assert saved["withdrawn_by"] is None
    assert saved["withdrawn_at"] is None
    assert saved["reopened_at"]
    audits = list(root.collection("audit_log")._docs.values())
    assert any(a["event"] == "assurance_reopened" and a["finding_id"] == "F-1"
               for a in audits)


def test_human_rejected_finding_stays_rejected(monkeypatch):
    from recoup_agent import db as db_module
    root = _fake_findings_db(monkeypatch, {
        "F-1": {"status": "rejected", "rejected_by": "owner@example.com",
                "created_at": "2026-09-15T00:00:00+00:00"}})
    db_module.save_findings("acct", [dict(_FRESH)])
    saved = root.collection("findings")._docs["F-1"]
    assert saved["status"] == "rejected"
    assert "reopened_at" not in saved
    assert root.collection("audit_log")._docs == {}


def test_stale_withdrawal_records_system_actor(fake_db):
    finding = {"finding_id": "F-X-202606-MIN", "customer_id": "X",
               "period": "2026-06", "type": "unenforced_minimum",
               "status": "open", "monthly_recoverable": 1000.0}
    _seed_complete_assurance_book(fake_db, finding)
    _evaluate_usage_event(fake_db)
    stored = fake_db.findings[finding["finding_id"]]
    assert stored["status"] == "rejected"
    assert stored["withdrawn_by"] == "system"
    assert stored["withdrawn_at"]
