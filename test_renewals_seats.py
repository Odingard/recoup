from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from recoup_agent import api
from recoup_agent.ingestion_doc import ContractEntitlements, Entitlement
from recoup_agent.normalizer import normalize_contract_entitlements
from recoup_agent.reconciliation import reconcile
from recoup_agent.renewals import build_renewal_calendar


@pytest.fixture(autouse=True)
def sample_mode(monkeypatch):
    monkeypatch.setenv("RECOUP_SAMPLE_MODE", "1")


def _ent(term_type, value=0.0, **kw):
    return Entitlement(term_type=term_type, value=value, confidence_score=0.95,
                       provenance=f"clause for {term_type}", **kw)


def test_normalizer_maps_new_term_types():
    norm = normalize_contract_entitlements(ContractEntitlements(
        customer_name="Acme Corp",
        entitlements=[
            _ent("term_start", effective_date="2025-01-01"),
            _ent("term_end", effective_date="2026-12-31"),
            _ent("auto_renewal", 12),
            _ent("renewal_notice_days", 60),
            _ent("committed_seats", 100),
            _ent("seat_price", 40.0),
        ],
    ))
    assert norm["term_start"] == "2025-01-01"
    assert norm["term_end"] == "2026-12-31"
    assert norm["auto_renew_months"] == 12
    assert norm["renewal_notice_days"] == 60
    assert norm["committed_seats"] == 100
    assert norm["seat_price"] == 40.0
    for key in ("term_start", "term_end", "auto_renew_months", "renewal_notice_days",
                "committed_seats", "seat_price"):
        assert norm["term_meta"][key]["confidence"] == 0.95
        assert norm["term_meta"][key]["provenance"].startswith("clause for")


def _contract(**over):
    c = {
        "customer_id": "acme", "customer_name": "Acme Corp",
        "committed_minimum_monthly": 5000.0,
        "included_units": 100, "overage_rate": 1.0,
        "annual_escalator_pct": None, "escalator_effective_date": None,
        "discounts": [],
        "term_meta": {"committed_minimum_monthly": {"confidence": 1.0, "provenance": "min clause"}},
        "clauses": {"committed_minimum": "min clause"},
    }
    c.update(over)
    return c


def _invoice(**over):
    inv = {"customer_id": "acme", "period": "2026-06", "base_charge": 5000.0,
           "overage_charge": 0.0, "discounts_applied": [], "credits_applied": []}
    inv.update(over)
    return inv


USAGE = {"customer_id": "acme", "period": "2026-06", "units": 50}


def test_rule5_post_term_review_only_without_autorenew():
    nr = []
    c = _contract(term_end="2026-05-31")
    reconcile(c, USAGE, _invoice(), "2026-06", needs_review=nr)
    assert any(i["term"] == "term_end" and "no auto-renewal" in i["reason"] for i in nr)


def test_rule5_silent_before_end_and_with_autorenew():
    nr = []
    reconcile(_contract(term_end="2026-12-31"), USAGE, _invoice(), "2026-06", needs_review=nr)
    assert not any(i["term"] == "term_end" for i in nr)
    nr = []
    reconcile(_contract(term_end="2026-05-31", auto_renew_months=12), USAGE, _invoice(),
              "2026-06", needs_review=nr)
    assert not any(i["term"] == "term_end" for i in nr)


def test_rule5_autorenew_escalator_no_date():
    nr = []
    c = _contract(term_end="2026-05-31", auto_renew_months=12, annual_escalator_pct=0.04,
                  escalator_effective_date=None)
    reconcile(c, USAGE, _invoice(), "2026-06", needs_review=nr)
    assert any("renewal pricing" in i["reason"] for i in nr)


def test_rule6_fires_when_no_base_but_other_lines():
    inv = _invoice(base_charge=0.0, overage_charge=250.0)
    findings = reconcile(_contract(), USAGE, inv, "2026-06", needs_review=[])
    rule6 = [f for f in findings if f["type"] == "missing_base_charge"]
    assert len(rule6) == 1
    assert rule6[0]["monthly_recoverable"] == 5000.0
    assert "billed base $0.00" in rule6[0]["math"]
    # Rule 1 must not also fire
    assert not any(f["type"] == "unenforced_minimum" for f in findings)


def test_rule6_does_not_fire_when_rule1_applies():
    inv = _invoice(base_charge=3000.0)
    findings = reconcile(_contract(), USAGE, inv, "2026-06", needs_review=[])
    assert [f["type"] for f in findings] == ["unenforced_minimum"]
    assert findings[0]["monthly_recoverable"] == 2000.0


def test_rule7_no_seat_line_review_note():
    nr = []
    c = _contract(committed_seats=100, seat_price=40.0,
                  term_meta={"committed_seats": {"confidence": 1.0, "provenance": "seats clause"},
                             "seat_price": {"confidence": 1.0, "provenance": "price clause"}})
    reconcile(c, USAGE, _invoice(), "2026-06", needs_review=nr)
    assert any(i["term"] == "committed_seats" and "no seat line" in i["reason"] for i in nr)


def test_rule7_committed_floor_and_usage_above():
    c = _contract(committed_seats=100, seat_price=40.0,
                  term_meta={"committed_seats": {"confidence": 1.0, "provenance": "seats clause"},
                             "seat_price": {"confidence": 1.0, "provenance": "price clause"}})
    # billed 95 seats, no seat usage -> committed floor 100
    findings = reconcile(c, USAGE, _invoice(seat_units=95), "2026-06", needs_review=[])
    seat = [f for f in findings if f["type"] == "underbilled_seats"]
    assert seat[0]["monthly_recoverable"] == 200.0
    # usage reports 120 active seats -> expected 120
    findings = reconcile(c, {"customer_id": "acme", "period": "2026-06", "units": 120,
                             "metric": "seats"}, _invoice(seat_units=95), "2026-06", needs_review=[])
    seat = [f for f in findings if f["type"] == "underbilled_seats"]
    assert seat[0]["monthly_recoverable"] == 1000.0
    assert "active 120" in seat[0]["math"]


def test_rule7_exact_match_silent():
    c = _contract(committed_seats=100, seat_price=40.0,
                  term_meta={"committed_seats": {"confidence": 1.0, "provenance": "seats clause"},
                             "seat_price": {"confidence": 1.0, "provenance": "price clause"}})
    findings = reconcile(c, USAGE, _invoice(seat_units=100), "2026-06", needs_review=[])
    assert not any(f["type"] == "underbilled_seats" for f in findings)


def test_renewal_calendar_states():
    today = date(2026, 9, 11)
    contracts = [
        {"customer_id": "a", "customer_name": "A", "term_end": "2026-10-15",
         "renewal_notice_days": 45},          # deadline 2026-08-31 -> notice_window_open
        {"customer_id": "b", "customer_name": "B", "term_end": "2026-12-31",
         "renewal_notice_days": 60},          # deadline 2026-11-01 -> upcoming_90d
        {"customer_id": "c", "customer_name": "C", "term_end": "2028-01-01"},  # later
        {"customer_id": "d", "customer_name": "D", "term_end": "2026-01-01"},  # expired
        {"customer_id": "e", "customer_name": "E"},                            # unknown
    ]
    rows = build_renewal_calendar(contracts, today)
    states = {r["customer_id"]: r["state"] for r in rows}
    assert states == {"a": "notice_window_open", "b": "upcoming_90d", "c": "later",
                      "d": "expired", "e": "unknown"}
    assert rows[-1]["customer_id"] == "e"  # unknown sorted last
    a = next(r for r in rows if r["customer_id"] == "a")
    assert a["notice_deadline"] == "2026-08-31"


def test_renewals_endpoint_sample_mode():
    res = TestClient(api.app).get("/api/renewals")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) >= 1
    assert any(r["term_end"] for r in rows)
    assert {r["state"] for r in rows} <= {"notice_window_open", "upcoming_90d", "later",
                                        "expired", "unknown"}
