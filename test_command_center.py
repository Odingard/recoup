"""Recovery Command Center: metrics partition, realized-value netting, ranking,
next-step matrix, expected/actual fields, API shape + lock redaction."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from recoup_agent import api, command_center as cc
from recoup_agent.command_center import (
    build_command_center, evidence_completeness, is_verified, next_step, rank_score)
from recoup_agent.pipeline import compute_findings_and_review
from recoup_agent.success_fee import compute_metrics

NOW = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _f(fid, status="open", amount=100.0, conf=0.9, cid="acme", name="Acme",
       clause=True, math=True, created="2026-06-01T00:00:00+00:00",
       ftype="unenforced_minimum", expected=None, actual=None, **kw):
    f = {"finding_id": fid, "customer_id": cid, "customer_name": name,
         "type": ftype, "title": ftype, "monthly_recoverable": amount,
         "currency": "USD", "status": status, "period": "2026-06",
         "confidence_score": conf, "created_at": created,
         "clause_ref": "committed_minimum",
         "clause_text": "clause" if clause else "",
         "provenance": "clause" if clause else "",
         "math": "m" if math else ""}
    if expected is not None:
        f["expected_value"] = expected
    if actual is not None:
        f["actual_value"] = actual
    f.update(kw)
    return f


def _real(fid, value, basis="cash_payment"):
    return {"finding_id": fid, "event_type": "realization",
            "recovery_basis": basis, "realized_value": value}


def test_metrics_partition():
    findings = [
        _f("o1", "open", 100, conf=0.9),                      # verified open
        _f("o2", "open", 50, conf=0.5),                       # open needs review
        _f("o3", "open", 40, conf=0.9, clause=False),         # open needs review
        _f("a1", "approved", 200),
        _f("i1", "invoiced", 300),
        _f("d1", "disputed", 70),
        _f("w1", "written_off", 60),
        _f("rj1", "rejected", 500),
        _f("r1", "recovered", 80),
    ]
    events = [_real("r1", 80)]
    out = build_command_center(findings, events, [], [])
    m = out["metrics"]
    assert m["potential_recoverable_value"] == 190.0
    assert m["verified_value"] == 100.0
    assert m["needs_review"] == 90.0
    assert m["approved"] == 200.0
    assert m["in_recovery"] == 370.0
    assert m["disputed"] == 70.0
    assert m["realized_value"] == 80.0
    assert m["written_off"] == 60.0
    # rejected excluded from metrics & executive summary but present as a case
    assert out["executive_summary"]["total_opportunity"] == 900.0
    assert out["executive_summary"]["open_cases"] == 6
    assert "rj1" in {c["finding_id"] for c in out["cases"]}
    stages = {s["stage"]: s for s in out["pipeline"]}
    assert stages["Potential"]["count"] == 3
    assert stages["Verified"]["count"] == 1
    assert stages["In Recovery"]["count"] == 2
    assert stages["Realized"] == {"stage": "Realized", "value": 80.0, "count": 1}


def test_realized_nets_reversals_and_matches_success_fee():
    findings = [_f("r1", "recovered", 100, recovered_at="2026-06-10T00:00:00+00:00")]
    events = [_real("r1", 100),
              {"finding_id": "r1", "event_type": "reversal",
               "recovery_basis": "cash_payment", "reversal_amount": 30}]
    out = build_command_center(findings, events, [], [])
    assert out["metrics"]["realized_value"] == 70.0
    assert compute_metrics(findings, events=events)["recovered_to_date"] == 70.0
    case = out["cases"][0]
    assert case["net_realized"] == 70.0
    assert len(case["realization_history"]) == 2


def test_exec_summary_rate_and_avg_days():
    findings = [
        _f("r1", "recovered", 100, created="2026-06-01T00:00:00+00:00",
           recovered_at="2026-06-11T00:00:00+00:00"),
        _f("r2", "recovered", 60, created="2026-06-01T00:00:00+00:00",
           recovered_at="2026-06-21T00:00:00+00:00"),
        _f("o1", "open", 40),
    ]
    out = build_command_center(findings, [], [], [], now=NOW)
    es = out["executive_summary"]
    assert es["total_opportunity"] == 200.0
    assert es["realized_value"] == 160.0  # fallback: recovered finding value
    assert es["recovery_rate"] == 0.8
    assert es["avg_days_to_recovery"] == 15.0

    empty = build_command_center([], [], [], [])
    assert empty["executive_summary"]["recovery_rate"] == 0
    assert empty["executive_summary"]["avg_days_to_recovery"] is None


def test_rank_ordering_and_components():
    f_big = _f("big", "open", 9000, conf=0.9)
    f_small = _f("small", "open", 100, conf=0.9)
    f_old = _f("old", "open", 100, conf=0.9, created="2026-01-01T00:00:00+00:00")
    f_new = _f("new", "open", 100, conf=0.9, created="2026-06-30T00:00:00+00:00")
    for f in (f_big, f_small, f_old, f_new):
        r = rank_score(f, NOW, evidence_completeness(f), 1.0)
        assert set(r) == {"score", "value", "confidence", "age_days",
                          "evidence", "status_weight"}
    assert rank_score(f_big, NOW, 1.0, 1.0)["score"] > \
        rank_score(f_small, NOW, 1.0, 1.0)["score"]
    assert rank_score(f_old, NOW, 1.0, 1.0)["score"] > \
        rank_score(f_new, NOW, 1.0, 1.0)["score"]
    out = build_command_center([f_small, f_big], [], [], [], now=NOW)
    assert out["cases"][0]["finding_id"] == "big"


def test_next_step_matrix():
    v = _f("x", "open", conf=0.9)
    nv = _f("x", "open", conf=0.5)
    assert next_step(v, []) == "Approve for recovery"
    assert next_step(nv, []) == "Review evidence / confirm term"
    assert next_step(_f("x", "approved"), []) == "Prepare recovery action"
    assert next_step(_f("x", "approved"), [],
                     [{"status": "draft"}]) == "Submit for approval"
    assert next_step(_f("x", "approved"), [],
                     [{"status": "pending_approval"}]) == "Approve recovery action"
    assert next_step(_f("x", "approved"), [],
                     [{"status": "approved"}]) == "Execute approved action"
    assert next_step(_f("x", "approved"), [],
                     [{"status": "sent"}]) == "Await counterparty response"
    assert next_step(_f("x", "invoiced"), []) == "Follow up on payment"
    assert next_step(_f("x", "disputed"), []) == "Resolve dispute"
    assert next_step(_f("x", "recovered"), []) == "Record realized value"
    assert next_step(_f("x", "recovered"), [_real("x", 10)]) == "Closed — verified realized"
    assert next_step(_f("x", "rejected"), []) == "None"
    assert next_step(_f("x", "written_off"), []) == "None"


def test_expected_actual_on_all_rule_findings():
    findings, _ = compute_findings_and_review(account_id=None)
    assert findings
    for f in findings:
        assert f["expected_value"] is not None, f["finding_id"]
        assert f["actual_value"] is not None, f["finding_id"]
        assert abs(f["expected_value"] - f["actual_value"]
                   - f["monthly_recoverable"]) < 0.01, f["finding_id"]


def test_sample_mode_command_center(monkeypatch):
    monkeypatch.setenv("RECOUP_SAMPLE_MODE", "1")
    client = TestClient(api.app)
    resp = client.get("/api/command-center", headers={"X-Recoup-Sample": "1"})
    assert resp.status_code == 200
    body = resp.json()
    for key in ("metrics", "pipeline", "cases", "executive_summary",
                "filters", "generated_at"):
        assert key in body
    assert body["mode"] == "sample"
    assert len(body["pipeline"]) == 5


def test_locked_account_blanks_evidence(monkeypatch):
    class _Db:
        def get_all_findings(self, a): return [_f("f1")]
        def get_all_contracts(self, a): return []
        def get_recovery_events(self, a): return []
        def get_audit_log(self, a): return []
        def get_assurance_status(self, a): return None
        def get_recovery_actions(self, a): return []
    db = _Db()
    for name in ("get_all_findings", "get_all_contracts", "get_recovery_events",
                 "get_audit_log", "get_assurance_status", "get_recovery_actions"):
        monkeypatch.setattr(api.db, name, getattr(db, name))
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    from firebase_admin import auth as firebase_auth
    monkeypatch.setattr(firebase_auth, "verify_id_token",
                        lambda t, **kw: {"uid": "u1", "account_id": "acct"})
    monkeypatch.setattr(api, "_proof_unlocked", lambda user: False)
    client = TestClient(api.app)
    resp = client.get("/api/command-center", headers={"Authorization": "Bearer x"})
    assert resp.status_code == 200
    case = resp.json()["cases"][0]
    assert case["locked"] is True
    assert case["evidence"]["clause_text"] is None
    assert case["evidence"]["math"] is None
    assert case["evidence"]["provenance"] is None
    resp2 = client.get("/api/command-center/cases/f1",
                       headers={"Authorization": "Bearer x"})
    assert resp2.status_code == 200
    assert resp2.json()["finding_id"] == "f1"
    assert client.get("/api/command-center/cases/ghost",
                      headers={"Authorization": "Bearer x"}).status_code == 404
