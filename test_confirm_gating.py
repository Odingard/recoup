"""Confirmation gating: a human-confirmed contract's extracted terms are
authoritative; low-confidence term_meta no longer withholds findings."""
import firebase_admin.auth as firebase_auth
from fastapi.testclient import TestClient

from recoup_agent import api, db as db_module
from recoup_agent.reconciliation import reconcile


def _contract(**overrides):
    contract = {
        "customer_id": "acme",
        "customer_name": "Acme Corp",
        "committed_minimum_monthly": 1000.0,
        "term_start": "2026-01-01",
        "term_end": "2026-12-31",
        "term_meta": {
            "committed_minimum_monthly": {
                "confidence": 0.6,
                "provenance": "Section 3.1 - minimum monthly fee of $1,000.",
            },
        },
    }
    contract.update(overrides)
    return contract


def _invoice():
    return {"customer_id": "acme", "period": "2026-06", "base_charge": 800.0}


def test_low_confidence_minimum_withholds_finding():
    needs_review = []
    findings = reconcile(_contract(), {}, _invoice(), "2026-06", needs_review=needs_review)
    assert findings == []
    gaps = [r for r in needs_review if r["term"] == "committed_minimum_monthly"]
    assert len(gaps) == 1


def test_confirmed_contract_produces_finding_at_full_amount():
    confirmed = reconcile(
        _contract(confirmed=True), {}, _invoice(), "2026-06", needs_review=[])
    full_conf = reconcile(
        _contract(term_meta={"committed_minimum_monthly": {
            "confidence": 1.0,
            "provenance": "Section 3.1 - minimum monthly fee of $1,000.",
        }}), {}, _invoice(), "2026-06", needs_review=[])
    assert len(confirmed) == 1
    assert confirmed[0]["type"] == "unenforced_minimum"
    assert confirmed[0]["monthly_recoverable"] == full_conf[0]["monthly_recoverable"] == 200.0


def test_confirmation_cannot_invent_missing_values():
    # Confirmed but the term value itself is absent -> still needs_review.
    needs_review = []
    findings = reconcile(
        _contract(confirmed=True, committed_minimum_monthly=None),
        {}, _invoice(), "2026-06", needs_review=needs_review)
    assert findings == []
    assert any(r["term"] == "committed_minimum_monthly" for r in needs_review)

    # Confirmed but the term already ended -> still needs_review for term_end.
    needs_review = []
    findings = reconcile(
        _contract(confirmed=True, term_end="2026-05-31"),
        {}, _invoice(), "2026-06", needs_review=needs_review)
    assert findings == []
    assert any(r["term"] == "term_end" for r in needs_review)


def test_save_contract_clears_stale_confirmation():
    cleared = db_module._contract_write_payload(
        {"customer_id": "acme", "committed_minimum_monthly": 900.0})
    assert cleared["confirmed"] is False
    assert cleared["confirmed_by"] is None
    assert cleared["confirmed_at"] is None

    explicit = {"customer_id": "acme", "confirmed": True,
                "confirmed_by": "ops@example.com", "confirmed_at": "2026-06-20T00:00:00+00:00"}
    assert db_module._contract_write_payload(explicit) == explicit


def _auth_client(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setattr(api, "_ensure_firebase_app", lambda: None)
    monkeypatch.setattr(
        firebase_auth, "verify_id_token",
        lambda token, check_revoked=False: {
            "uid": "uid-1",
            "email": "owner@example.com",
            "account_id": "acct-1",
        },
    )
    return TestClient(api.app)


def test_confirm_endpoint_returns_assurance_block(monkeypatch):
    client = _auth_client(monkeypatch)
    monkeypatch.setattr(api.db, "get_all_contracts", lambda _a: [_contract()])
    monkeypatch.setattr(api.db, "confirm_contract", lambda *args: {
        "customer_id": "acme",
        "confirmed": True,
        "confirmed_by": "owner@example.com",
        "confirmed_at": "2026-06-20T00:00:00+00:00",
    })
    calls = []

    def fake_assure(account_id, source, triggers, customer_id, period, payload):
        calls.append((account_id, source, triggers, customer_id, period, payload))
        return [{"event_id": "evt-1", "status": "evaluated"}]

    monkeypatch.setattr(api, "_assure", fake_assure)
    resp = client.post("/api/contracts/acme/confirm",
                       headers={"Authorization": "Bearer token"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "confirmed"
    assert body["assurance"]["events"] == [{"event_id": "evt-1", "status": "evaluated"}]
    assert calls == [("acct-1", "contract/confirm", "agreement_amendment",
                      "acme", None,
                      {"customer_id": "acme", "confirmed": True,
                       "confirmed_by": "owner@example.com",
                       "confirmed_at": "2026-06-20T00:00:00+00:00"})]
