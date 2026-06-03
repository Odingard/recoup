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


if __name__ == "__main__":
    test_acme_total_is_14200()
    test_acme_has_three_findings()
    test_globex_is_clean()
    test_initech_missed_escalator()
    print("All reconciliation tests passed.")
