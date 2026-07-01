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
                effective_date="2026-03-31",
                confidence_score=0.88,
                provenance="Section 7.4",
            ),
        ],
    )

    normalized = normalize_contract_entitlements(contract)

    assert normalized["customer_id"] == "acme_corp"
    assert normalized["customer_name"] == "Acme Corp"
    assert normalized["committed_minimum_monthly"] == 50000
    assert normalized["discounts"][0]["expires"] == "2026-03-31"
    assert normalized["term_meta"]["committed_minimum_monthly"]["confidence"] == 0.91
    assert normalized["term_meta"]["committed_minimum_monthly"]["provenance"] == "Section 3.1"
    assert normalized["term_meta"]["discounts"]["confidence"] == 0.88
