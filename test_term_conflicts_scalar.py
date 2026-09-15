"""Generalized multi-value conflict gate: two distinct values for any
single-value term in one agreement must fail closed to 'Which term governs?'
exactly like committed minimums do."""
import firebase_admin.auth as firebase_auth
from fastapi.testclient import TestClient

from recoup_agent import api
from recoup_agent.ingestion_doc import ContractEntitlements, Entitlement
from recoup_agent.normalizer import (
    detect_term_conflicts, normalize_contract_entitlements)
from recoup_agent.reconciliation import reconcile


def _ent(term_type, value=0.0, effective_date=None, page=None, section=None,
         provenance="clause"):
    return Entitlement(term_type=term_type, value=value,
                       effective_date=effective_date, page=page,
                       section_ref=section, confidence_score=0.95,
                       provenance=provenance)


def _normalize(ents):
    return normalize_contract_entitlements(
        ContractEntitlements(customer_name="Scalar Corp", entitlements=ents))


def test_two_undated_seat_values_conflict():
    normalized = _normalize([
        _ent("committed_seats", 240, page=4, section="4.1"),
        _ent("committed_seats", 200, page=12, section="Exhibit A"),
        _ent("seat_price", 65.0, page=4),
    ])
    assert normalized["committed_seats"] is None
    assert normalized["seat_price"] == 65.0
    conflicts = normalized["term_conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["term"] == "committed_seats"
    assert {c["value"] for c in conflicts[0]["candidates"]} == {240, 200}
    assert normalized["term_meta"]["committed_seats"]["conflict"] is True


def test_differently_dated_values_latest_wins_no_conflict():
    normalized = _normalize([
        _ent("committed_seats", 240, effective_date="2026-01-01", page=4),
        _ent("committed_seats", 200, effective_date="2026-07-01", page=12),
    ])
    assert normalized["committed_seats"] == 200
    assert "term_conflicts" not in normalized
    assert normalized["term_meta"]["committed_seats"]["page"] == 12


def test_identical_duplicates_dedupe():
    normalized = _normalize([
        _ent("committed_seats", 240, page=4),
        _ent("committed_seats", 240, page=4),
    ])
    assert normalized["committed_seats"] == 240
    assert "term_conflicts" not in normalized


def test_two_term_end_dates_conflict():
    normalized = _normalize([
        _ent("term_end", effective_date="2026-12-31", page=9),
        _ent("term_end", effective_date="2027-12-31", page=12),
    ])
    assert normalized["term_end"] is None
    conflicts = normalized["term_conflicts"]
    assert len(conflicts) == 1 and conflicts[0]["term"] == "term_end"
    assert {c["value"] for c in conflicts[0]["candidates"]} == \
        {"2026-12-31", "2027-12-31"}


def test_detect_term_conflicts_preserves_scalar_conflicts():
    contract = _conflicted_seat_contract()
    contract["minimum_schedule"] = [
        {"amount": 15000.0, "effective_date": "2026-03-01"},
        {"amount": 18000.0, "effective_date": "2026-03-01"},
    ]
    detect_term_conflicts(contract)
    terms = [c["term"] for c in contract["term_conflicts"]]
    assert "committed_seats" in terms
    assert "committed_minimum" in terms


def _conflicted_seat_contract():
    return {
        "customer_id": "scalar", "customer_name": "Scalar Corp",
        "committed_seats": None, "seat_price": 65.0,
        "term_start": "2026-01-01", "term_end": "2026-12-31",
        "term_meta": {"committed_seats": {"confidence": 0.0, "conflict": True}},
        "term_conflicts": [{
            "term": "committed_seats", "effective_date": None,
            "candidates": [
                {"value": 240, "page": 4, "section_ref": "4.1"},
                {"value": 200, "page": 12, "section_ref": "Exhibit A"}]}],
        "unresolved_terms": [],
        "confirmed": False,
        "clauses": {"seats": "Customer commits to seats."},
    }


def test_reconcile_seat_conflict_single_review_no_finding():
    contract = _conflicted_seat_contract()
    needs_review = []
    findings = reconcile(
        contract,
        {"customer_id": "scalar", "period": "2026-06", "units": 240.0},
        {"customer_id": "scalar", "period": "2026-06",
         "seat_units": 200.0, "base_charge": 13000.0, "amount_billed": 13000.0},
        "2026-06", needs_review=needs_review)
    assert not any(f["type"] == "underbilled_seats" for f in findings)
    seat_reviews = [r for r in needs_review if r["term"] == "committed_seats"]
    assert len(seat_reviews) == 1
    assert "240" in seat_reviews[0]["reason"] and "200" in seat_reviews[0]["reason"]
    assert "choose which governs" in seat_reviews[0]["reason"]
    # no generic missing-seats review on top of the conflict line
    assert not any(r["term"] == "committed_seats"
                   and "missing" in r["reason"] for r in needs_review)


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


def test_confirm_scalar_conflict_409_then_200(monkeypatch):
    client = _auth_client(monkeypatch)
    contract = _conflicted_seat_contract()
    saved = _wire_contract(monkeypatch, contract)

    resp = client.post("/api/contracts/scalar/confirm",
                       headers={"Authorization": "Bearer token"}, json={})
    assert resp.status_code == 409
    assert resp.json()["detail"]["status"] == "needs_review"
    assert contract.get("confirmed") is not True

    resp = client.post(
        "/api/contracts/scalar/confirm",
        headers={"Authorization": "Bearer token"},
        json={"resolutions": {"terms": {
            "committed_seats": {"value": 240, "page": 4}}}})
    assert resp.status_code == 200
    assert saved["committed_seats"] == 240
    assert saved["term_conflicts"] == []
    assert saved["term_meta"]["committed_seats"]["page"] == 4
    assert saved["term_resolutions"]["choices"]["terms"]["committed_seats"]["value"] == 240
