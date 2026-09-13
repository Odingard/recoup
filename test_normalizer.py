from recoup_agent.ingestion_doc import ContractEntitlements, Entitlement
from recoup_agent.normalizer import normalize_contract_entitlements


def test_normalizer_maps_entitlements_and_metadata():
    contract = ContractEntitlements(
        customer_name="Acme Corp",
        entitlements=[
            Entitlement(
                term_type="committed_minimum",
                value=50000,
                effective_date=None,
                confidence_score=0.91,
                provenance="Section 3.1",
            ),
            Entitlement(
                term_type="discount",
                value=0.05,
                end_date="2026-03-31",
                confidence_score=0.88,
                provenance="Section 7.4",
            ),
        ],
    )

    normalized = normalize_contract_entitlements(contract)

    assert normalized["customer_id"] == "acme"
    assert normalized["customer_name"] == "Acme Corp"
    assert normalized["committed_minimum_monthly"] == 50000
    assert normalized["discounts"][0]["expires"] == "2026-03-31"
    assert normalized["term_meta"]["committed_minimum_monthly"]["confidence"] == 0.91
    assert normalized["term_meta"]["committed_minimum_monthly"]["provenance"] == "Section 3.1"
    assert normalized["term_meta"]["discounts"]["confidence"] == 0.88


def test_normalizer_uses_latest_minimum_for_display_and_combines_confidence():
    contract = ContractEntitlements(
        customer_name="Acme Corp",
        entitlements=[
            Entitlement(
                term_type="committed_minimum",
                value=9000,
                effective_date=None,
                confidence_score=0.93,
                provenance="Section 3.1: $9,000 monthly minimum",
            ),
            Entitlement(
                term_type="committed_minimum",
                value=6000,
                effective_date="2026-03-01",
                confidence_score=0.82,
                provenance="Amendment 1: $6,000 monthly minimum effective March 1, 2026",
            ),
        ],
    )

    normalized = normalize_contract_entitlements(contract)

    assert normalized["committed_minimum_monthly"] == 6000
    assert normalized["term_meta"]["committed_minimum_monthly"] == {
        "confidence": 0.82,
        "provenance": "Amendment 1: $6,000 monthly minimum effective March 1, 2026",
    }
    assert all("confidence" not in entry for entry in normalized["minimum_schedule"])
