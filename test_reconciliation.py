"""Locks the demo numbers so a refactor can't silently break them.

Run:  python -m pytest test_reconciliation.py   (or)   python test_reconciliation.py
"""
from recoup_agent.pipeline import compute_findings


def test_acme_total_is_14200():
    findings = compute_findings("2026-06")
    acme = sum(f["monthly_recoverable"] for f in findings if f["customer_id"] == "acme")
    assert acme == 14200.0, f"expected 14200, got {acme}"


def test_acme_has_three_findings():
    findings = compute_findings("2026-06")
    acme = [f["type"] for f in findings if f["customer_id"] == "acme"]
    assert set(acme) == {"unenforced_minimum", "unbilled_overage", "expired_discount"}, acme


def test_globex_is_clean():
    findings = compute_findings("2026-06")
    globex = [f for f in findings if f["customer_id"] == "globex"]
    assert globex == [], f"expected no findings for globex, got {globex}"


def test_initech_missed_escalator():
    findings = compute_findings("2026-06")
    initech = sum(f["monthly_recoverable"] for f in findings if f["customer_id"] == "initech")
    assert initech == 1000.0, f"expected 1000, got {initech}"


def test_findings_carry_period_confidence_and_provenance():
    findings = compute_findings("2026-06")
    acme = [f for f in findings if f["customer_id"] == "acme"]
    assert acme, "expected findings for acme"
    for f in acme:
        assert f["period"] == "2026-06"
        assert isinstance(f["confidence_score"], float)
        assert 0.0 <= f["confidence_score"] <= 1.0
        assert f["provenance"].strip()
        assert f["provenance"] == f["clause_text"]


def test_ungrounded_finding_becomes_needs_review():
    from recoup_agent.reconciliation import reconcile

    contract = {"customer_id": "x", "customer_name": "X", "committed_minimum_monthly": 8000}
    invoice = {"base_charge": 4800}
    needs_review = []
    findings = reconcile(contract, {}, invoice, "2026-06", needs_review=needs_review)
    assert findings == []
    minimum_gaps = [r for r in needs_review if r["term"] == "committed_minimum_monthly"]
    assert len(minimum_gaps) == 1
    assert minimum_gaps[0]["amount"] == 3200.0


def _escalator_contract():
    return {
        "customer_id": "esc", "customer_name": "Esc Co",
        "committed_minimum_monthly": 10000,
        "annual_escalator_pct": 0.04,
        "escalator_effective_date": "2024-05-01",
        "clauses": {"escalator": "Fees increase 4% annually.",
                    "committed_minimum": "Minimum monthly charge $10,000."},
    }


def test_escalator_compounds_three_years():
    from recoup_agent.reconciliation import reconcile

    invoice = {"base_charge": 10000, "overage_charge": 0, "discounts_applied": []}
    findings = reconcile(_escalator_contract(), {}, invoice, "2026-06")
    esc = [f for f in findings if f["type"] == "missed_escalator"]
    assert len(esc) == 1
    assert esc[0]["monthly_recoverable"] == 1248.64
    assert esc[0]["escalator_steps"] == 3


def test_escalator_partial_application():
    from recoup_agent.reconciliation import reconcile

    invoice = {"base_charge": 10400, "overage_charge": 0, "discounts_applied": []}
    findings = reconcile(_escalator_contract(), {}, invoice, "2026-06")
    esc = [f for f in findings if f["type"] == "missed_escalator"]
    assert len(esc) == 1
    assert esc[0]["monthly_recoverable"] == 848.64


def test_escalator_no_double_count_with_minimum():
    from recoup_agent.reconciliation import reconcile

    invoice = {"base_charge": 9000, "overage_charge": 0, "discounts_applied": []}
    findings = reconcile(_escalator_contract(), {}, invoice, "2026-06")
    by_type = {f["type"]: f for f in findings}
    assert by_type["unenforced_minimum"]["monthly_recoverable"] == 1000.0
    assert by_type["missed_escalator"]["monthly_recoverable"] == 1248.64


def test_escalator_no_finding_before_effective_date():
    from recoup_agent.reconciliation import reconcile

    invoice = {"base_charge": 10000, "overage_charge": 0, "discounts_applied": []}
    findings = reconcile(_escalator_contract(), {}, invoice, "2024-04")
    assert not [f for f in findings if f["type"] == "missed_escalator"]


if __name__ == "__main__":
    test_acme_total_is_14200()
    test_acme_has_three_findings()
    test_globex_is_clean()
    test_initech_missed_escalator()
    print("All reconciliation tests passed.")
