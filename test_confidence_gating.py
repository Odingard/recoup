from recoup_agent.ingestion_doc import ContractEntitlements, Entitlement
from recoup_agent.normalizer import normalize_contract_entitlements
from recoup_agent.reconciliation import reconcile


def _contract(min_confidence: float) -> dict:
    return normalize_contract_entitlements(
        ContractEntitlements(
            customer_name="Review Co",
            entitlements=[
                Entitlement(
                    term_type="committed_minimum",
                    value=50000,
                    confidence_score=min_confidence,
                    provenance="minimum clause",
                ),
                Entitlement(
                    term_type="included_units",
                    value=1000,
                    confidence_score=0.99,
                    provenance="included units clause",
                ),
                Entitlement(
                    term_type="overage_rate",
                    value=3.5,
                    confidence_score=0.99,
                    provenance="overage clause",
                ),
            ],
        )
    )


def test_low_confidence_term_goes_to_review_and_skips_finding():
    needs_review = []
    findings = reconcile(
        _contract(0.2),
        {"customer_id": "review_co", "customer_name": "Review Co", "period": "2026-06", "units": 1000},
        {"customer_id": "review_co", "customer_name": "Review Co", "period": "2026-06", "base_charge": 1000, "overage_charge": 0, "discounts_applied": []},
        "2026-06",
        needs_review=needs_review,
    )

    assert findings == []
    assert needs_review and needs_review[0]["term"] == "committed_minimum_monthly"


def test_high_confidence_term_flows_to_finding():
    needs_review = []
    findings = reconcile(
        _contract(0.99),
        {"customer_id": "review_co", "customer_name": "Review Co", "period": "2026-06", "units": 1000},
        {"customer_id": "review_co", "customer_name": "Review Co", "period": "2026-06", "base_charge": 1000, "overage_charge": 0, "discounts_applied": []},
        "2026-06",
        needs_review=needs_review,
    )

    assert [f["type"] for f in findings] == ["unenforced_minimum"]
    assert needs_review == []
