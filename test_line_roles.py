from recoup_agent.line_roles import classify_line
from recoup_agent.reconciliation import reconcile


def test_classify_line_roles():
    discounts = [{"name": "Launch promo", "value": 0.2, "type": "percent"}]
    assert classify_line("Prorated charge for June", 100) == "proration"
    assert classify_line("Base fee", 50, proration=True) == "proration"
    assert classify_line("Sales tax", 64) == "tax"
    assert classify_line("GST", 10) == "tax"
    assert classify_line("Service credit", -100) == "credit"
    assert classify_line("Refund — goodwill", -50) == "credit"
    assert classify_line("Launch promo", -200, contract_discounts=discounts) == "discount"
    assert classify_line("Unknown negative line", -25) == "discount"
    assert classify_line("Metered usage", 30, usage_type="metered") == "overage"
    assert classify_line("API overage", 30) == "overage"
    assert classify_line("Platform subscription", 5000) == "base"


def test_prorated_invoice_skips_minimum_and_escalator():
    contract = {
        "customer_id": "c", "customer_name": "C",
        "committed_minimum_monthly": 8000,
        "annual_escalator_pct": 0.04,
        "escalator_effective_date": "2026-05-01",
        "clauses": {"committed_minimum": "min $8,000", "escalator": "4%/yr"},
    }
    invoice = {"base_charge": 4000, "prorated": True, "proration_amount": -4000.0}
    nr = []
    findings = reconcile(contract, {}, invoice, "2026-06", needs_review=nr)
    assert not findings
    assert any(r["term"] == "base_charge" for r in nr)


def test_credits_flagged_but_never_discounts():
    contract = {
        "customer_id": "c", "customer_name": "C",
        "committed_minimum_monthly": 8000,
        "clauses": {"committed_minimum": "min $8,000"},
    }
    invoice = {
        "base_charge": 4800,
        "credits_applied": [{"description": "Service credit", "amount": 500.0}],
        "discounts_applied": [],
    }
    nr = []
    findings = reconcile(contract, {}, invoice, "2026-06", needs_review=nr)
    assert len(findings) == 1 and findings[0]["type"] == "unenforced_minimum"
    assert any(r["term"] == "credits_applied" and r["amount"] == 500.0 for r in nr)
    # Credits alone (no findings) → no credits review noise
    nr2 = []
    reconcile(contract, {}, {"base_charge": 9000,
                           "credits_applied": [{"description": "Service credit", "amount": 500}]},
              "2026-06", needs_review=nr2)
    assert not [r for r in nr2 if r["term"] == "credits_applied"]


def test_tiered_overage_math():
    contract = {
        "customer_id": "c", "customer_name": "C",
        "included_units": 10000,
        "overage_tiers": [
            {"up_to": 5000, "rate": 0.05, "provenance": "tier one"},
            {"up_to": None, "rate": 0.04, "provenance": "tier two"},
        ],
        "clauses": {"overage": "tiered overage"},
        "term_meta": {"overage_tiers": {"confidence": 1.0}},
    }
    usage = {"units": 22000}
    invoice = {"base_charge": 5000, "overage_charge": 0, "discounts_applied": []}
    findings = reconcile(contract, usage, invoice, "2026-06")
    ov = [f for f in findings if f["type"] == "unbilled_overage"]
    assert len(ov) == 1
    assert ov[0]["monthly_recoverable"] == 530.00
    assert ov[0]["overage_tiers"][0]["up_to"] == 5000
    assert "0–5,000 × $0.05 = $250.00" in ov[0]["math"]


def test_tax_line_excluded_from_base():
    from recoup_agent.book_loader import normalize_invoice

    raw = {"customer_id": "c", "period": "2026-06",
           "line_items": [{"description": "Platform fee", "amount": 8000},
                          {"description": "Sales tax", "amount": 640}]}
    inv = normalize_invoice(raw, {"discounts": []})
    assert inv["base_charge"] == 8000
    assert inv["tax_excluded"] == 640
    contract = {"customer_id": "c", "customer_name": "C",
                "committed_minimum_monthly": 8000,
                "clauses": {"committed_minimum": "min"}}
    findings = reconcile(contract, {}, inv, "2026-06")
    assert not [f for f in findings if f["type"] == "unenforced_minimum"]
