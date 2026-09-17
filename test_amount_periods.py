from decimal import Decimal

import pytest

from recoup_agent.extraction.extractor import DocumentProfile, PageAnchoredEntitlement, _dedupe
from recoup_agent.extraction.graph import ExtractedDocument, assemble
from recoup_agent.ingest_bulk import BulkResult, _append_bundle
from recoup_agent.ingestion_doc import ContractEntitlements, Entitlement
from recoup_agent.normalizer import normalize_contract_entitlements
from recoup_agent.reconciliation import reconcile


def _term(term_type="committed_minimum", **changes):
    return Entitlement(**{
        "term_type": term_type, "value": 120000, "effective_date": "2024-01-01",
        "provenance": "For Contract Year 1, the annual Committed Fees are $120,000.",
        "confidence_score": 1.0, "page": 120, "section_ref": "F.2", **changes,
    })


def _normalize(*terms):
    return normalize_contract_entitlements(
        ContractEntitlements(customer_name="Example", entitlements=list(terms)))


@pytest.mark.parametrize("period,value,quote,expected", [
    ("year", 96000, "The annual committed fee is $96,000.", 8000),
    ("quarter", 15000, "The quarterly committed fee is $15,000.", 5000),
    ("month", 8500, "The minimum is $8,500 per month.", 8500),
    (None, 120000, "The annual Committed Fees are $120,000.", 10000),
    ("year", 10000, "An annual minimum of $10,000.", 833.33),
])
def test_literal_amounts_are_converted_in_python(period, value, quote, expected):
    contract = _normalize(_term(value=value, amount_period=period, provenance=quote))
    assert contract["committed_minimum_monthly"] == expected
    meta = contract["term_meta"]["committed_minimum_monthly"]
    assert meta["source_value"] == value
    assert meta["provenance"] == quote
    assert "cents/month" in meta["normalization_formula"]


@pytest.mark.parametrize("period,quote", [
    ("month", "The annual minimum is $120,000."),
    ("year", "The monthly minimum is $120,000."),
    ("unknown", "The minimum is $120,000."),
    ("year", "The minimum is $120,000."),
    (None, "Annual fees of $120,000 and monthly fees of $12,000."),
])
def test_ambiguous_or_contradictory_period_cannot_generate_findings(period, quote):
    contract = _normalize(_term(amount_period=period, provenance=quote))
    assert contract["committed_minimum_monthly"] is None
    assert contract["term_meta"]["committed_minimum_monthly"]["confidence"] == 0
    review = []
    assert reconcile(contract, {}, {"base_charge": 8500}, "2026-01", needs_review=review) == []
    assert any(row["term"] == "committed_minimum_monthly" for row in review)


def test_annual_and_monthly_extractions_dedupe_after_conversion():
    contract = _normalize(
        _term(amount_period="year"),
        _term(amount_period="month", value=10000, provenance="The monthly fee is $10,000."),
    )
    assert contract["committed_minimum_monthly"] == 10000
    assert len(contract["minimum_schedule"]) == 1
    assert contract["term_conflicts"] == []


def test_equal_numbers_with_different_periods_are_not_deduplicated_by_extractor():
    annual = PageAnchoredEntitlement(**_term(amount_period="year").model_dump())
    monthly = annual.model_copy(update={"amount_period": "month"})
    assert len(_dedupe([annual, monthly])) == 2


def test_saved_msa_terms_produce_correct_monthly_shortfall_without_model():
    terms = [
        _term(),
        _term("escalator", value=0.04, section_ref="F.3",
              provenance="On each anniversary of the Effective Date, the then-current Committed Fees will automatically increase by four percent (4.0%)."),
        _term("term_start", value=0, page=1, provenance="EFFECTIVE DATE January 1, 2024"),
    ]
    bundle = assemble([ExtractedDocument(
        "agreement.pdf",
        DocumentProfile(role="master", counterparty="Example", effective_date="2024-01-01"),
        entitlements=terms,
        structural_verification={"state": "Verified"},
    )])[0]
    result = BulkResult()
    _append_bundle(result, bundle)
    contract = result.contracts[0]
    assert contract["committed_minimum_monthly"] == 10000
    assert contract["escalator_effective_date"] == "2025-01-01"
    assert contract["source_entitlements"][0]["value"] == 120000
    totals = []
    for month in range(1, 7):
        findings = reconcile(contract, {}, {"base_charge": 8500}, f"2026-{month:02}")
        totals.append(sum(Decimal(str(f["monthly_recoverable"])) for f in findings))
        assert totals[-1] == 2316
        escalator = next(f for f in findings if f["type"] == "missed_escalator")
        assert escalator["escalator_steps"] == 2
        assert escalator["expected_value"] == 10816
    assert sum(totals) == 13896
    assert reconcile(contract, {}, {"base_charge": 10000}, "2024-01") == []
    assert reconcile(contract, {}, {"base_charge": 10400}, "2025-01") == []


def test_explicit_first_increase_is_not_shifted_again():
    contract = _normalize(
        _term(),
        _term("term_start", value=0, provenance="Effective Date: January 1, 2024."),
        _term("escalator", value=0.04, effective_date="2025-01-01",
              provenance="Fees increase 4% from January 1, 2025 and on each anniversary."),
    )
    assert contract["escalator_effective_date"] == "2025-01-01"


def test_ambiguous_escalator_commencement_requires_review():
    contract = _normalize(
        _term(),
        _term("term_start", value=0),
        _term("escalator", value=0.04, provenance="Fees increase 4% annually."),
    )
    review = []
    assert reconcile(contract, {}, {"base_charge": 10000}, "2026-01", needs_review=review) == []
    assert any(row["term"] == "annual_escalator_pct/escalator_effective_date" for row in review)


def test_first_anniversary_of_leap_day():
    contract = _normalize(
        _term("term_start", value=0, effective_date="2024-02-29"),
        _term("escalator", value=0.04, effective_date="2024-02-29",
              provenance="Fees increase 4% on each anniversary of the Effective Date."),
    )
    assert contract["escalator_effective_date"] == "2025-02-28"
