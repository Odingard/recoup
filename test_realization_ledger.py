"""Closed-loop realization: case ledger math, lineage, double-count guards,
recovery metrics, outcome records, API wiring."""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from recoup_agent import api
from recoup_agent.billing import realized_value as rv
from recoup_agent.realization_ledger import (assert_no_double_count,
                                           case_ledger, recovery_metrics)
from recoup_agent.recovery_actions.models import new_action
from recoup_agent.success_fee import compute_metrics


def _finding(status="approved", amount=1000.0, fid="F-acme-202606-MIN",
             created="2026-06-01T00:00:00+00:00", cid="acme",
             ftype="unenforced_minimum"):
    return {"finding_id": fid, "customer_id": cid, "customer_name": f"{cid} Co",
            "type": ftype, "title": ftype, "monthly_recoverable": amount,
            "currency": "USD", "status": status, "period": "2026-06",
            "confidence_score": 0.9, "clause_ref": "committed_minimum",
            "clause_text": "clause", "math": "m", "provenance": "p",
            "created_at": created}


def _real(f, value, basis="cash_payment", action_id=None, at=None):
    return rv.new_realization("acct", f, recovery_basis=basis,
                              realized_value=value, realized_at=at,
                              recovery_action_id=action_id).to_dict()


# 1. single full realization → resolved_full
def test_full_realization_resolved():
    f = _finding(status="recovered", amount=1000)
    e = _real(f, 1000, at="2026-06-21T00:00:00+00:00")
    led = case_ledger(f, [e], [])
    assert led["resolution_status"] == "resolved_full"
    assert led["outstanding_value"] == 0.0
    assert led["net_realized"] == 1000.0
    assert led["days_to_recovery"] == 20.0
    assert led["realization_date"] == "2026-06-21T00:00:00+00:00"
    assert led["integrity"] == "ok"


def test_date_only_realization_with_aware_audit_is_timezone_safe():
    f = _finding(status="recovered", amount=1000,
                 created="2026-09-01T00:00:00+00:00")
    event = _real(f, 1000, at="2026-09-14")
    audit = [{"finding_id": f["finding_id"], "decision": "approved",
              "ts": "2026-09-02T12:00:00+00:00"}]
    led = case_ledger(f, [event], [], audit)
    assert isinstance(led["days_to_recovery"], float)
    assert led["days_to_recovery"] == 13.0
    assert led["first_action_date"] == "2026-09-02T12:00:00+00:00"


# 2. partial + reversal → partially_realized; recovered under requested → settled
def test_partial_and_settled():
    f = _finding(status="invoiced", amount=1000)
    e1 = _real(f, 400, at="2026-06-10T00:00:00+00:00")
    e2 = _real(f, 300, basis="settlement", at="2026-06-15T00:00:00+00:00")
    rev = rv.new_reversal("acct", rv.RecoveryRealizationEvent.from_dict(e2),
                          reversal_amount=100, reversal_reference="cr-1",
                          reason="partial credit").to_dict()
    led = case_ledger(f, [e1, e2, rev], [])
    assert led["realized_value"] == 700.0
    assert led["reversed_value"] == 100.0
    assert led["net_realized"] == 600.0
    assert led["resolution_status"] == "partially_realized"
    assert led["outstanding_value"] == 400.0

    f2 = {**f, "status": "recovered"}
    led2 = case_ledger(f2, [e1, e2, rev], [])
    assert led2["resolution_status"] == "settled"
    assert led2["settlement_shortfall"] == 400.0
    assert led2["outstanding_value"] == 0.0


# 3. double-count guards
def test_no_double_count():
    f = _finding(status="recovered")
    e = _real(f, 500)
    orig = rv.RecoveryRealizationEvent.from_dict(e)
    with pytest.raises(ValueError):
        rv.new_reversal("acct", orig, reversal_amount=600,
                        reversal_reference="x", reason=None,
                        existing_events=[orig])
    dup = [e, dict(e)]
    assert assert_no_double_count(dup).startswith("violation")
    over = [e, {"recovery_event_id": "r1", "event_type": "reversal",
                "finding_id": f["finding_id"],
                "reverses_event_id": e["recovery_event_id"],
                "reversal_amount": 600}]
    assert "violation" in assert_no_double_count(over)
    led = case_ledger(f, over, [])
    assert led["integrity"].startswith("violation")


# 4. closed statuses
def test_closed_and_unverified():
    assert case_ledger(_finding(status="written_off"), [], [])[
        "resolution_status"] == "written_off"
    assert case_ledger(_finding(status="rejected"), [], [])[
        "outstanding_value"] == 0.0
    led = case_ledger(_finding(status="recovered"), [], [])
    assert led["resolution_status"] == "resolved_unverified"
    assert led["outstanding_value"] == 0.0


# 5. lineage
def test_lineage_on_realization_and_reversal():
    f = _finding()
    e = rv.new_realization("acct", f, recovery_basis="cash_payment",
                           realized_value=200, recovery_action_id="ra_1")
    lin = e.lineage
    assert lin["agreement"] == "acme"
    assert lin["financial_right"] == "unenforced_minimum"
    assert lin["recovery_case"] == f["finding_id"]
    assert lin["recovery_action"] == "ra_1"
    assert lin["realization_event"] == e.recovery_event_id
    r = rv.new_reversal("acct", e, reversal_amount=50,
                        reversal_reference="cr", reason=None)
    assert r.recovery_action_id == "ra_1"
    assert r.lineage["recovery_action"] == "ra_1"
    assert r.lineage["realization_event"] == r.recovery_event_id


# 6. recovery_metrics sums and groupings
def test_recovery_metrics_totals():
    a = new_action("acct", _finding(), "corrective_invoice",
                   created_by="u").to_dict()
    f1 = _finding(status="recovered", fid="F-1", amount=1000)
    f2 = _finding(status="invoiced", fid="F-2", amount=500, cid="beta",
                  ftype="unbilled_overage")
    e1 = _real(f1, 1000, at="2026-06-11T00:00:00+00:00",
               action_id=a["recovery_action_id"])
    e2 = _real(f2, 250, at="2026-06-16T00:00:00+00:00")
    m = recovery_metrics([f1, f2], [e1, e2], [a])
    assert m["total_opportunity"] == 1500.0
    assert m["realized_value"] == 1250.0
    assert m["recovery_rate"] == round(1250 / 1500, 4)
    assert m["avg_recovery_per_case"] == 625.0
    assert m["avg_time_to_recovery"] == pytest.approx(12.5)
    strat = {s["action_type"]: s for s in m["recovery_by_action_strategy"]}
    assert strat["corrective_invoice"]["realized"] == 1000.0
    assert strat["unattributed"]["realized"] == 250.0
    assert sum(s["realized"] for s in m["recovery_by_action_strategy"]) == 1250.0
    assert sum(s["realized"] for s in m["recovery_by_right_type"]) == 1250.0
    assert sum(s["realized"] for s in m["recovery_by_customer"]) == 1250.0
    assert m["resolution_mix"]["resolved_full"] == 1
    assert m["resolution_mix"]["partially_realized"] == 1


# 7. fee parity with compute_metrics (unchanged 20%)
def test_fee_matches_compute_metrics():
    f = _finding(status="recovered")
    e = _real(f, 800)
    led = case_ledger(f, [e], [])
    assert led["fee"]["net_fee"] == rv.net_fee([e]) == 160.0
    assert led["fee"]["fee_pct"] == rv.SUCCESS_FEE_PCT
    cm = compute_metrics([f], events=[e])
    assert led["fee"]["net_fee"] == cm["success_fee_to_date"]


# --- API-level with fake db ---

class FakeDb:
    def __init__(self):
        self.findings = {"acct-a": {}, "acct-b": {}}
        self.events = {"acct-a": [], "acct-b": []}
        self.actions = {"acct-a": {}, "acct-b": {}}
        self.outcomes = {"acct-a": {}, "acct-b": {}}
        self.audit = []

    def get_finding(self, a, fid):
        f = self.findings.get(a, {}).get(fid)
        return copy.deepcopy(f) if f else None

    def get_all_findings(self, a):
        return [copy.deepcopy(x) for x in self.findings.get(a, {}).values()]

    def get_all_contracts(self, a):
        return []

    def get_recovery_events(self, a, fid=None):
        out = self.events.get(a, [])
        if fid:
            out = [e for e in out if e.get("finding_id") == fid]
        return [copy.deepcopy(e) for e in out]

    def save_recovery_event(self, a, e, *, expected_finding=None, finding_fields=None, event_name=None):
        if any(x["recovery_event_id"] == e["recovery_event_id"]
               for x in self.events.setdefault(a, [])):
            return False
        self.events[a].append(copy.deepcopy(e))
        if finding_fields:
            self.findings[a][e["finding_id"]].update(finding_fields)
        return True

    def update_recovery_event_fields(self, a, eid, fields, event):
        for x in self.events.get(a, []):
            if x["recovery_event_id"] == eid:
                x.update(fields)

    def update_finding_fields(self, a, fid, fields, event):
        self.findings[a][fid].update(fields)
        self.audit.append({"event": event, "finding_id": fid})

    def transition_finding_status(self, a, fid, new_status, event, fields=None):
        f = self.findings.get(a, {}).get(fid)
        if f is None:
            raise api.db.FindingNotFound(fid)
        try:
            from recoup_agent.recovery import assert_transition
            assert_transition(f.get("status", "open"), new_status)
        except ValueError:
            raise api.db.IllegalTransition(f.get("status", "open"), new_status)
        f["status"] = new_status
        if fields:
            f.update(fields)
        self.audit.append({"event": event, "finding_id": fid,
                           "decision": new_status})
        return dict(f)

    def get_recovery_action(self, a, aid):
        x = self.actions.get(a, {}).get(aid)
        return copy.deepcopy(x) if x else None

    def get_recovery_actions(self, a, finding_id=None):
        out = list(self.actions.get(a, {}).values())
        if finding_id:
            out = [x for x in out if x.get("finding_id") == finding_id]
        return [copy.deepcopy(x) for x in out]

    def save_recovery_action(self, a, act):
        self.actions.setdefault(a, {})[act["recovery_action_id"]] = \
            copy.deepcopy(act)

    def update_recovery_action(self, a, act, event):
        self.save_recovery_action(a, act)
        self.audit.append({"event": event})

    def get_audit_log(self, a, finding_id=None):
        out = self.audit
        if finding_id:
            out = [x for x in out if x.get("finding_id") == finding_id]
        return list(out)

    def get_assurance_status(self, a):
        return None

    def save_outcome_record(self, a, record):
        self.outcomes.setdefault(a, {})[record["finding_id"]] = dict(record)

    def get_outcome_record(self, a, fid):
        r = self.outcomes.get(a, {}).get(fid)
        return dict(r) if r else None


@pytest.fixture
def client(monkeypatch):
    fake = FakeDb()
    fake.findings["acct-a"]["F-acme-202606-MIN"] = _finding()
    names = [n for n in dir(fake) if not n.startswith("_")
             and hasattr(api.db, n)]
    for name in names:
        monkeypatch.setattr(api.db, name, getattr(fake, name))
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    from firebase_admin import auth as firebase_auth
    monkeypatch.setattr(
        firebase_auth, "verify_id_token",
        lambda t, **kw: {"uid": "u", "email": "u@t.co",
                         "account_id": {"ta": "acct-a"}.get(t, "acct-b")})
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setattr(api.recoup_billing, "charge_success_fee_for_event",
                        lambda *a, **kw: None)
    c = TestClient(api.app)
    c.fake_db = fake
    return c


def test_api_ledger_lineage_and_action_link(client):
    h = {"Authorization": "Bearer ta"}
    # create + approve + execute an action so it can be linked
    r = client.post("/api/findings/F-acme-202606-MIN/recovery-actions",
                    json={"action_type": "corrective_invoice"}, headers=h)
    aid = r.json()["recovery_action_id"]
    client.post(f"/api/recovery-actions/{aid}/submit", headers=h)
    client.post(f"/api/recovery-actions/{aid}/approve", headers=h)
    client.post(f"/api/recovery-actions/{aid}/execute",
                json={"channel": "manual", "external_reference": "sent-1"},
                headers=h)

    # wrong finding linkage → 422
    other = new_action("acct-a",
                       {**_finding(), "finding_id": "F-other"},
                       "credit_request", created_by="u").to_dict()
    client.fake_db.actions["acct-a"][other["recovery_action_id"]] = other
    r = client.post("/api/findings/F-acme-202606-MIN/recovery-events",
                    json={"recovery_basis": "cash_payment",
                          "realized_value": 100,
                          "recovery_action_id": other["recovery_action_id"]},
                    headers=h)
    assert r.status_code == 422

    # linked realization
    r = client.post("/api/findings/F-acme-202606-MIN/recovery-events",
                    json={"recovery_basis": "cash_payment",
                          "realized_value": 1000,
                          "recovery_action_id": aid}, headers=h)
    assert r.status_code == 200
    ev = r.json()
    assert ev["recovery_action_id"] == aid
    assert ev["lineage"]["recovery_action"] == aid
    # action history got realization_linked, status stays 'sent'
    act = client.fake_db.actions["acct-a"][aid]
    assert act["status"] == "sent"
    assert act["history"][-1]["event"] == "realization_linked"

    # ledger endpoint
    led = client.get("/api/recovery-cases/F-acme-202606-MIN/ledger",
                     headers=h).json()
    assert led["resolution_status"] == "resolved_full"
    assert led["net_realized"] == 1000.0
    assert led["events"][0]["recovery_action_id"] == aid

    # outcome record written on event create and contains no free text
    rec = client.fake_db.outcomes["acct-a"]["F-acme-202606-MIN"]
    for key in rec:
        assert key not in ("clause_text", "math", "draft_communication")
    assert rec["schema_version"] == 1
    r = client.get("/api/recovery-cases/F-acme-202606-MIN/outcome", headers=h)
    assert r.status_code == 200
    assert r.json()["resolution_status"] == "resolved_full"
    # tenant isolation: account B cannot read A's outcome
    assert client.get("/api/recovery-cases/F-acme-202606-MIN/outcome",
                      headers={"Authorization": "Bearer tb"}).status_code == 404

    # recovery metrics endpoint
    m = client.get("/api/metrics/recovery", headers=h).json()
    assert m["realized_value"] == 1000.0
    strat = {s["action_type"]: s["realized"]
             for s in m["recovery_by_action_strategy"]}
    assert strat["corrective_invoice"] == 1000.0


def test_outcome_written_on_reject(client):
    h = {"Authorization": "Bearer ta"}
    r = client.post("/api/findings/F-acme-202606-MIN/reject",
                    json={"status": "rejected", "reason": "no"}, headers=h)
    assert r.status_code == 200
    rec = client.fake_db.outcomes["acct-a"].get("F-acme-202606-MIN")
    assert rec is not None
    assert rec["resolution_status"] == "rejected"


def test_command_center_cases_carry_ledger(client):
    r = client.get("/api/command-center",
                   headers={"Authorization": "Bearer ta"})
    case = next(c for c in r.json()["cases"]
                if c["finding_id"] == "F-acme-202606-MIN")
    assert "ledger" in case
    assert case["ledger"]["resolution_status"] == "open" or \
        case["ledger"]["potential_value"] == 1000.0
    assert "recovery_metrics" in r.json()["executive_summary"]
