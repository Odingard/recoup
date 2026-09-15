"""Fail-closed handling of conflicting / undated committed_minimum terms:
detect_term_conflicts, reconciliation needs_review, confirm resolutions."""
import firebase_admin.auth as firebase_auth
from fastapi.testclient import TestClient

from recoup_agent import api
from recoup_agent.normalizer import detect_term_conflicts
from recoup_agent.reconciliation import minimum_for_period, reconcile


def _conflicted_contract():
    """The stored cat5 shape: undated amendment + two same-date minimums."""
    return {
        "customer_id": "cat5", "customer_name": "Cat5 Corp",
        "committed_minimum_monthly": 15000.0,
        "term_start": "2026-01-01", "term_end": "2027-01-01",
        "term_meta": {"committed_minimum_monthly": {
            "confidence": 0.9, "provenance": "Section 5.2"}},
        "minimum_schedule": [
            {"amount": 16500.0, "effective_date": None, "page": 17,
             "provenance": "Amendment - Monthly Commitment"},
            {"amount": 15000.0, "effective_date": "2026-03-01", "page": 5,
             "section_ref": "5.2", "provenance": "Section 5.2"},
            {"amount": 18000.0, "effective_date": "2026-03-01", "page": 13,
             "section_ref": "A.1", "provenance": "Exhibit A.1"},
        ],
    }


def test_detect_term_conflicts_flags_and_prunes():
    contract = _conflicted_contract()
    detect_term_conflicts(contract)

    assert len(contract["term_conflicts"]) == 1
    conflict = contract["term_conflicts"][0]
    assert conflict["term"] == "committed_minimum"
    assert conflict["effective_date"] == "2026-03-01"
    assert {c["amount"] for c in conflict["candidates"]} == {15000.0, 18000.0}

    assert len(contract["unresolved_terms"]) == 1
    unresolved = contract["unresolved_terms"][0]
    assert unresolved["amount"] == 16500.0
    assert unresolved["reason"] == "undated_amendment"

    assert [e["amount"] for e in contract["minimum_schedule"]] == [15000.0, 18000.0]


def test_detect_term_conflicts_dedupes_identical_entries():
    contract = _conflicted_contract()
    contract["minimum_schedule"].append(
        {"amount": 15000.0, "effective_date": "2026-03-01",
         "provenance": "duplicate"})
    detect_term_conflicts(contract)
    conflict = contract["term_conflicts"][0]
    assert len(conflict["candidates"]) == 2  # 15k deduped, not flagged


def test_detect_term_conflicts_ignores_sole_undated_entry():
    contract = _conflicted_contract()
    contract["minimum_schedule"] = [
        {"amount": 16500.0, "effective_date": None, "provenance": "only"}]
    detect_term_conflicts(contract)
    assert contract["term_conflicts"] == []
    assert contract["unresolved_terms"] == []
    assert contract["minimum_schedule"][0]["amount"] == 16500.0


def test_confirmed_conflicted_contract_still_fails_closed():
    needs_review = []
    contract = _conflicted_contract()
    contract["confirmed"] = True
    findings = reconcile(
        contract, {},
        {"customer_id": "cat5", "period": "2026-06", "base_charge": 14000.0},
        "2026-06", needs_review=needs_review)
    assert findings == []
    gaps = [r for r in needs_review if r["term"] == "committed_minimum"]
    assert len(gaps) == 1
    assert "Conflicting minimums" in gaps[0]["reason"]
    assert "Undated amendment" in gaps[0]["reason"]
    assert gaps[0]["term_conflicts"] and gaps[0]["unresolved_terms"]


def test_resolved_schedule_drives_minimum_for_period():
    contract = _conflicted_contract()
    detect_term_conflicts(contract)
    # Resolution: 18k governs from 2026-03; the undated amendment applies 2026-06.
    contract["minimum_schedule"] = [
        {"amount": 18000.0, "effective_date": "2026-03-01"},
        {"amount": 16500.0, "effective_date": "2026-06-01"},
    ]
    contract["term_conflicts"] = []
    contract["unresolved_terms"] = []
    assert minimum_for_period(contract, "2026-05")[0] == 18000.0
    assert minimum_for_period(contract, "2026-06")[0] == 16500.0


def _auth_client(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(
        firebase_auth, "verify_id_token",
        lambda token, check_revoked=False: {
            "uid": "uid-1", "email": "owner@example.com", "account_id": "acct-1"})
    monkeypatch.setattr(api, "_assure", lambda *a, **k: [])
    return TestClient(api.app)


def _wire_contract(monkeypatch, contract):
    saved = {}
    monkeypatch.setattr(api.db, "get_all_contracts", lambda _a: [contract])
    monkeypatch.setattr(api.db, "save_contract",
                        lambda _a, payload: saved.update(payload))
    monkeypatch.setattr(api.db, "confirm_contract", lambda _a, cid, actor: {
        **contract, "confirmed": True, "confirmed_by": actor,
        "confirmed_at": "2026-09-15T00:00:00+00:00"})
    return saved


def test_confirm_conflict_without_resolutions_409(monkeypatch):
    client = _auth_client(monkeypatch)
    contract = _conflicted_contract()
    detect_term_conflicts(contract)
    _wire_contract(monkeypatch, contract)
    resp = client.post("/api/contracts/cat5/confirm",
                       headers={"Authorization": "Bearer token"}, json={})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["status"] == "needs_review"
    assert detail["term_conflicts"] and detail["unresolved_terms"]
    assert contract.get("confirmed") is not True


def test_confirm_with_full_resolutions_200(monkeypatch):
    client = _auth_client(monkeypatch)
    contract = _conflicted_contract()
    detect_term_conflicts(contract)
    saved = _wire_contract(monkeypatch, contract)
    resp = client.post(
        "/api/contracts/cat5/confirm",
        headers={"Authorization": "Bearer token"},
        json={"resolutions": {
            "committed_minimum": [
                {"effective_date": "2026-03-01", "amount": 18000.0},
                {"effective_date": "2026-06", "amount": 16500.0}],
        }})
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"
    assert saved["term_conflicts"] == []
    assert saved["unresolved_terms"] == []
    assert saved["committed_minimum_monthly"] == 18000.0
    assert saved["term_resolutions"]["resolved_by"] == "owner@example.com"
    schedule = {e["effective_date"]: e["amount"]
                for e in saved["minimum_schedule"]}
    assert schedule == {"2026-03-01": 18000.0, "2026-06-01": 16500.0}
    # Provenance copied from the chosen candidate.
    march = next(e for e in saved["minimum_schedule"]
                 if e["effective_date"] == "2026-03-01")
    assert march["section_ref"] == "A.1" and march["page"] == 13


def test_confirm_partial_resolutions_409(monkeypatch):
    client = _auth_client(monkeypatch)
    contract = _conflicted_contract()
    detect_term_conflicts(contract)
    _wire_contract(monkeypatch, contract)
    # Resolves the conflict but leaves the undated amendment unaddressed.
    resp = client.post(
        "/api/contracts/cat5/confirm",
        headers={"Authorization": "Bearer token"},
        json={"resolutions": {"committed_minimum": [
            {"effective_date": "2026-03-01", "amount": 15000.0}]}})
    assert resp.status_code == 409


def test_confirm_dismissed_undated_amendment_200(monkeypatch):
    client = _auth_client(monkeypatch)
    contract = _conflicted_contract()
    detect_term_conflicts(contract)
    saved = _wire_contract(monkeypatch, contract)
    resp = client.post(
        "/api/contracts/cat5/confirm",
        headers={"Authorization": "Bearer token"},
        json={"resolutions": {
            "committed_minimum": [
                {"effective_date": "2026-03-01", "amount": 18000.0}],
            "dismissed": [{"term": "committed_minimum",
                           "amount": 16500.0, "page": 17}],
        }})
    assert resp.status_code == 200
    assert [e["amount"] for e in saved["minimum_schedule"]] == [18000.0]


def _ent(**kw):
    from recoup_agent.ingestion_doc import Entitlement
    defaults = dict(term_type="committed_minimum", value=0.0, label=None,
                    effective_date=None, start_date=None, end_date=None,
                    tier_up_to=None, confidence_score=1.0,
                    provenance="", page=None, section_ref=None,
                    verification=None, source_file=None)
    defaults.update(kw)
    return Entitlement(**defaults)


def _normalized(ents):
    from recoup_agent.ingestion_doc import ContractEntitlements
    from recoup_agent.normalizer import normalize_contract_entitlements
    return normalize_contract_entitlements(
        ContractEntitlements(customer_name="Cat5 Corp", entitlements=ents))


def test_normalize_minimum_meta_carries_governing_verification():
    v15 = {"quote_found": True, "page_matched": True,
           "model_check": "supports", "final_confidence": 1.0}
    v18 = {"quote_found": True, "page_matched": True,
           "model_check": "supports", "final_confidence": 0.95}
    contract = _normalized([
        _ent(value=15000.0, effective_date="2026-01-01", confidence_score=0.9,
             provenance="Section 5.2", page=5, section_ref="5.2",
             verification=v15),
        _ent(value=18000.0, effective_date="2026-03-01", confidence_score=1.0,
             provenance="Exhibit A.1", page=13, section_ref="A.1",
             verification=v18),
    ])
    meta = contract["term_meta"]["committed_minimum_monthly"]
    # Governing entry = latest effective date ($18k @ 2026-03-01).
    assert meta["page"] == 13 and meta["section_ref"] == "A.1"
    assert meta["verification"] == v18
    assert meta["confidence"] == 0.9  # min over schedule entries
    assert contract["term_conflicts"] == []
    assert all("verification" in e for e in contract["minimum_schedule"])


def test_unresolved_undated_entry_does_not_drag_meta_confidence():
    contract = _normalized([
        _ent(value=16500.0, confidence_score=0.7, provenance="Amendment",
             page=17),
        _ent(value=15000.0, effective_date="2026-03-01", confidence_score=1.0,
             provenance="Section 5.2", page=5, section_ref="5.2"),
        _ent(value=18000.0, effective_date="2026-03-01", confidence_score=1.0,
             provenance="Exhibit A.1", page=13, section_ref="A.1"),
    ])
    # The undated amendment lands in unresolved_terms, not the schedule.
    assert [u["amount"] for u in contract["unresolved_terms"]] == [16500.0]
    meta = contract["term_meta"]["committed_minimum_monthly"]
    assert meta["confidence"] == 1.0
    assert contract["term_conflicts"]  # same-date conflict still flagged


def test_confirm_resolutions_rebuild_meta_from_chosen_candidate(monkeypatch):
    client = _auth_client(monkeypatch)
    contract = _conflicted_contract()
    ver = {"quote_found": True, "page_matched": True,
           "model_check": "supports", "final_confidence": 1.0}
    contract["minimum_schedule"][2]["verification"] = ver  # $18k / A.1 / p.13
    detect_term_conflicts(contract)
    saved = _wire_contract(monkeypatch, contract)
    resp = client.post(
        "/api/contracts/cat5/confirm",
        headers={"Authorization": "Bearer token"},
        json={"resolutions": {
            "committed_minimum": [
                {"effective_date": "2026-03-01", "amount": 18000.0}],
            "dismissed": [{"term": "committed_minimum",
                           "amount": 16500.0, "page": 17}],
        }})
    assert resp.status_code == 200
    meta = saved["term_meta"]["committed_minimum_monthly"]
    assert meta["page"] == 13 and meta["section_ref"] == "A.1"
    assert meta["verification"] == ver
    assert meta["provenance"] == "Exhibit A.1"
