"""Amendment handling: minimum_schedule resolves the in-force committed minimum."""
from recoup_agent.reconciliation import minimum_for_period, reconcile


def _contract(schedule):
    return {
        "customer_id": "volt", "customer_name": "Volt Robotics",
        "committed_minimum_monthly": 9000, "included_units": 30, "overage_rate": 250.0,
        "annual_escalator_pct": 0.0, "escalator_effective_date": None, "discounts": [],
        "minimum_schedule": schedule,
        "clauses": {"committed_minimum": "", "overage": "", "discount": "", "escalator": ""},
    }


def test_amended_minimum_not_flagged():
    contract = _contract([
        {"amount": 9000, "effective_date": "2025-01-01", "provenance": "original"},
        {"amount": 6000, "effective_date": "2025-09-01", "provenance": "amendment"},
    ])
    usage = {"customer_id": "volt", "period": "2026-06", "units": 22}
    invoice = {"customer_id": "volt", "period": "2026-06", "base_charge": 6000,
               "overage_charge": 0, "discounts_applied": []}
    findings = reconcile(contract, usage, invoice, "2026-06")
    assert [f for f in findings if f["type"] == "unenforced_minimum"] == []


def test_schedule_order_does_not_matter():
    forward = _contract([
        {"amount": 9000, "effective_date": "2025-01-01", "provenance": "original"},
        {"amount": 6000, "effective_date": "2025-09-01", "provenance": "amendment"},
    ])
    reversed_ = _contract([
        {"amount": 6000, "effective_date": "2025-09-01", "provenance": "amendment"},
        {"amount": 9000, "effective_date": "2025-01-01", "provenance": "original"},
    ])
    assert minimum_for_period(forward, "2026-06") == minimum_for_period(reversed_, "2026-06")
    assert minimum_for_period(forward, "2026-06")[0] == 6000


def test_earlier_period_uses_original_minimum():
    contract = _contract([
        {"amount": 9000, "effective_date": "2025-01-01", "provenance": "original"},
        {"amount": 6000, "effective_date": "2025-09-01", "provenance": "amendment"},
    ])
    assert minimum_for_period(contract, "2025-06")[0] == 9000
