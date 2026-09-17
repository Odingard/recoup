from decimal import Decimal
from itertools import permutations

import pytest

from recoup_agent.book_loader import normalize_invoice
from recoup_agent import api
from recoup_agent.document_quality import LowConfidenceGateException
from recoup_agent.extraction.extractor import DocumentProfile
from recoup_agent.extraction.graph import ExtractedDocument, assemble
from recoup_agent.ingestion_doc import ContractEntitlements, Entitlement
from recoup_agent.ingest_csv import load_invoices_csv, load_usage_csv
from recoup_agent.identity import CustomerResolver
from recoup_agent.normalizer import normalize_contract_entitlements
from recoup_agent.pipeline import run_book
from recoup_agent.reconciliation import build_expected_ledger, reconcile, scalar_for_period


def term(kind, value, scope="platform", effective=None, **changes):
    return Entitlement(**{
        "term_type": kind, "value": value, "scope": scope,
        "effective_date": effective, "confidence_score": 0.95,
        "provenance": f"The {scope} {kind} is {value} per month.",
        **changes,
    })


def documents():
    return [
        ExtractedDocument("msa.pdf", DocumentProfile(
            role="master", counterparty="Example", effective_date="2024-01-01",
            order_form_precedence=True,
        ), entitlements=[
            term("committed_minimum", 8000, effective="2024-01-01"),
            term("included_units", 200000, effective="2024-01-01"),
            term("overage_rate", 0.08, effective="2024-01-01"),
        ]),
        ExtractedDocument("minimum.pdf", DocumentProfile(
            role="amendment", counterparty="Example", effective_date="2025-02-01",
        ), entitlements=[
            term("committed_minimum", 11000, effective="2025-02-01"),
        ]),
        ExtractedDocument("rate.pdf", DocumentProfile(
            role="amendment", counterparty="Example", effective_date="2025-07-01",
        ), entitlements=[
            term("overage_rate", 0.08, end_date="2025-06-30"),
            term("overage_rate", 0.05, effective="2025-07-01"),
            term("overage_rate", 0.08),
        ]),
        ExtractedDocument("order.pdf", DocumentProfile(
            role="order_form", counterparty="Example", effective_date="2025-07-01",
        ), entitlements=[
            term("committed_seats", 275, "enterprise_workspace",
                 "2025-07-01", end_date="2026-06-30"),
            term("seat_price", 42, "enterprise_workspace",
                 "2025-07-01", end_date="2026-06-30"),
        ]),
    ]


def contract(docs=None):
    bundle = assemble(docs or documents())[0]
    return normalize_contract_entitlements(ContractEntitlements(
        customer_name=bundle.customer_name, entitlements=bundle.entitlements,
    ))


def invoice(period="2025-08", overage=1800, quantity=None):
    return normalize_invoice({
        "customer_id": "example", "period": period,
        "line_items": [
            {"description": "Enterprise Workspace - 250 seats @ $42",
             "amount": 10500, "quantity": 250},
            {"description": "Metered usage overage", "amount": overage,
             **({"quantity": quantity} if quantity is not None else {})},
        ],
    }, None)


@pytest.mark.parametrize("period,units,billed,expected", [
    ("2025-08", 260000, 1800, Decimal("13250.00")),
    ("2025-09", 280000, 2400, Decimal("13650.00")),
])
def test_separate_platform_seats_and_amended_overage(period, units, billed, expected):
    findings = reconcile(
        contract(), {"units": units}, invoice(period, billed), period,
    )
    assert sum(Decimal(str(f["monthly_recoverable"])) for f in findings) == expected
    by_type = {f["type"]: f for f in findings}
    assert by_type["missing_base_charge"]["monthly_recoverable"] == 11000
    assert by_type["underbilled_seats"]["monthly_recoverable"] == 1050
    rate = by_type["unbilled_overage"]
    assert rate["expected_rate"] == 0.05
    assert rate["derived_actual_rate"] == 0.03
    assert rate["quantity_basis"] == "metered_overage"
    assert rate["variance_status"] == "TARIFF_MISMATCH_ERROR"
    assert rate["variance"] == -(expected - Decimal(12050))


def test_total_is_26900_and_ledger_does_not_need_billing():
    agreement = contract()
    total = Decimal(0)
    for period, used, billed in [("2025-08", 260000, 1800), ("2025-09", 280000, 2400)]:
        ledger = build_expected_ledger(agreement, {"units": used}, period)
        assert {row["kind"] for row in ledger} == {"base", "seat", "overage"}
        expected = sum(Decimal(str(row["expected_amount"])) for row in ledger)
        total += expected - Decimal(10500 + billed)
    assert total == Decimal("26900")


def test_document_and_entitlement_order_do_not_select_the_rate():
    docs = documents()
    for ordering in permutations(docs):
        normalized = contract(list(ordering))
        assert normalized["overage_rate"] == 0.05
        assert not normalized.get("term_conflicts")
    for ordering in permutations(docs[2].entitlements):
        docs[2].entitlements = list(ordering)
        normalized = contract(docs)
        assert scalar_for_period(normalized, "overage_rate", "2025-06", "overage_rate")[0] == 0.08
        assert scalar_for_period(normalized, "overage_rate", "2025-08", "overage_rate")[0] == 0.05


def test_future_and_expired_order_form_charges_are_not_active():
    agreement = contract()
    for period in ("2025-06", "2026-07"):
        ledger = build_expected_ledger(agreement, {"units": 260000}, period)
        assert not any(row["kind"] == "seat" for row in ledger)
    assert scalar_for_period(agreement, "overage_rate", "2023-12", "overage_rate")[0] is None


def test_execution_date_is_used_when_effective_date_is_absent():
    docs = documents()
    docs[1].profile = docs[1].profile.model_copy(update={
        "effective_date": None, "execution_date": "2025-02-01",
    })
    docs[1].entitlements[0] = docs[1].entitlements[0].model_copy(
        update={"effective_date": None}
    )
    assert contract(docs)["committed_minimum_monthly"] == 11000


def test_active_minimum_confidence_does_not_inherit_expired_terms():
    docs = documents()
    docs[0].entitlements[0].confidence_score = 0.2
    findings = reconcile(contract(docs), {"units": 260000}, invoice(), "2025-08")
    assert any(f["type"] == "missing_base_charge" for f in findings)


def test_low_confidence_active_rate_is_reviewed():
    docs = documents()
    docs[2].entitlements[1].confidence_score = 0.2
    review = []
    findings = reconcile(contract(docs), {"units": 260000}, invoice(), "2025-08", review)
    assert not any(f["type"] == "unbilled_overage" for f in findings)
    assert any("low-confidence" in item["reason"] for item in review)


def test_conflicting_active_rate_cannot_generate_recoverable_findings():
    docs = documents()
    docs[2].entitlements.append(term("overage_rate", 0.07, effective="2025-07-01"))
    review = []
    assert reconcile(contract(docs), {"units": 260000}, invoice(), "2025-08", review) == []
    assert any(item["term"] == "overage_rate" for item in review)


def test_explicit_invoice_quantity_drives_reverse_rate_calculation():
    findings = reconcile(
        contract(), {"units": 260000}, invoice(quantity=60000), "2025-08",
    )
    rate = next(f for f in findings if f["type"] == "unbilled_overage")
    assert rate["derived_actual_rate"] == 0.03
    assert rate["quantity_basis"] == "invoice"


def test_csv_ingestion_to_existing_rights_pipeline(tmp_path):
    agreement = contract()
    resolver = CustomerResolver([agreement])
    billing = tmp_path / "billing.csv"
    billing.write_text(
        "invoice date,customer,line description,amount\n"
        "2025-08-01,Example,Enterprise Workspace - 250 seats @ $42,10500.00\n"
        "2025-08-31,Example,Metered usage overage - August,1800.00\n"
        "2025-09-01,Example,Enterprise Workspace - 250 seats @ $42,10500.00\n"
        "2025-09-30,Example,Metered usage overage - September,2400.00\n"
    )
    metering = tmp_path / "usage.csv"
    metering.write_text("period,units\n2025-08,260000\n2025-09,280000\n")
    invoices, billing_review = load_invoices_csv(billing, resolver)
    usage, usage_review = load_usage_csv(metering, resolver, default_customer="Example")
    findings, review = run_book([agreement], usage, invoices)
    assert not billing_review and not usage_review and not review
    assert sum(Decimal(str(f["monthly_recoverable"]))
               for rows in findings.values() for f in rows) == Decimal("26900")
    assert all(f.get("right_id") for rows in findings.values() for f in rows)
    billing.write_text(billing.read_text() + billing.read_text().splitlines()[1] + "\n")
    duplicated, _ = load_invoices_csv(billing, resolver)
    august = next(row for row in duplicated if row["period"] == "2025-08")
    assert reconcile(agreement, {"units": 260000}, august, "2025-08", []) == []


def test_explicit_order_form_precedence_only_replaces_generic_seats():
    docs = documents()
    docs[0].entitlements.extend([
        term("committed_seats", 200, None, "2024-01-01"),
        term("seat_price", 40, None, "2024-01-01"),
    ])
    ledger = build_expected_ledger(contract(docs), {"units": 260000}, "2025-08")
    seats = [row for row in ledger if row["kind"] == "seat"]
    assert len(seats) == 1
    assert seats[0]["expected_amount"] == 11550
    docs.append(ExtractedDocument("later-rate.pdf", DocumentProfile(
        role="amendment", effective_date="2025-09-01", counterparty="Example",
    ), entitlements=[term("seat_price", 45, "enterprise_workspace", "2025-09-01")]))
    ledger = build_expected_ledger(contract(docs), {"units": 260000}, "2025-09")
    assert next(row for row in ledger if row["kind"] == "seat")["expected_amount"] == 12375


def test_human_resolution_updates_the_dated_state_without_erasing_history():
    docs = documents()
    docs[2].entitlements.append(term("overage_rate", 0.07, effective="2025-07-01"))
    agreement = contract(docs)
    assert api._resolve_scalar_conflicts(
        agreement, {"terms": {"overage_rate": {"value": 0.05}}}
    ) == []
    assert scalar_for_period(agreement, "overage_rate", "2025-06", "overage_rate")[0] == 0.08
    assert scalar_for_period(agreement, "overage_rate", "2025-08", "overage_rate")[0] == 0.05
    assert sum(f["monthly_recoverable"] for f in reconcile(
        agreement, {"units": 260000}, invoice(), "2025-08",
    )) == 13250


@pytest.mark.parametrize("changes", [
    {"incomplete": True}, {"currency": "EUR"}, {"currency_mixed": True},
    {"prorated": True},
])
def test_uncertain_invoice_data_cannot_generate_recovery(changes):
    actual = {**invoice(), **changes}
    assert reconcile(contract(), {"units": 260000}, actual, "2025-08", []) == []


@pytest.mark.parametrize("quantity", [0, -1, float("nan")])
def test_invalid_line_quantity_never_falls_back_to_metered_usage(quantity):
    assert reconcile(contract(), {"units": 260000}, invoice(quantity=quantity), "2025-08", []) == []


def test_structural_hold_still_blocks_direct_reconciliation():
    agreement = {**contract(), "verification_state": "Needs_Verification"}
    with pytest.raises(LowConfidenceGateException):
        reconcile(agreement, {"units": 260000}, invoice(), "2025-08")


def test_tiers_and_escalators_keep_their_existing_calculations():
    agreement = contract()
    agreement["overage_tiers"] = [
        {"up_to": 50000, "rate": 0.05}, {"up_to": None, "rate": 0.03},
    ]
    agreement["term_meta"]["overage_tiers"] = {
        "confidence": 1, "provenance": "First 50,000 at $0.05, then $0.03.",
    }
    findings = reconcile(agreement, {"units": 260000}, invoice(), "2025-08")
    assert next(f for f in findings if f["type"] == "unbilled_overage")["monthly_recoverable"] == 1000
    agreement = contract()
    agreement.update(annual_escalator_pct=0.04, escalator_effective_date="2025-01-01")
    agreement["term_meta"]["annual_escalator_pct"] = {
        "confidence": 1, "provenance": "Platform fees increase 4% annually.",
    }
    agreement["term_meta"]["escalator_effective_date"] = {"confidence": 1}
    findings = reconcile(agreement, {"units": 260000}, invoice(), "2025-08")
    assert next(f for f in findings if f["type"] == "missed_escalator")["monthly_recoverable"] == 440


def test_date_entitlements_and_seat_credits_are_preserved():
    docs = documents()
    docs[0].entitlements.append(term("term_start", 0, effective="2024-01-01"))
    agreement = contract(docs)
    actual = invoice()
    credited = normalize_invoice({
        "customer_id": "example", "period": "2025-08",
        "line_items": [{"description": "Credit for seats", "amount": -100}],
    }, agreement)
    assert credited["line_items"][0]["role"] == "credit"
    actual["line_items"].extend(credited["line_items"])
    actual["credits_applied"] = credited["credits_applied"]
    review = []
    findings = reconcile(agreement, {"units": 260000}, actual, "2025-08", review)
    assert sum(f["monthly_recoverable"] for f in findings) == 13250
    assert any(row["term"] == "credits_applied" for row in review)


def test_equal_total_with_incorrect_unit_rate_is_still_reviewed():
    review = []
    reconcile(contract(), {"units": 260000}, invoice(overage=3000, quantity=100000), "2025-08", review)
    assert any(row.get("variance_status") == "TARIFF_MISMATCH_ERROR" for row in review)


def test_confirming_terms_does_not_assign_one_meter_to_multiple_products():
    agreement = contract()
    for kind in ("included_units", "overage_rate"):
        agreement["variable_schedules"][kind].extend([
            {**entry, "scope": "other_product"}
            for entry in list(agreement["variable_schedules"][kind])
        ])
    agreement["confirmed"] = True
    review = []
    findings = reconcile(agreement, {"units": 260000}, invoice(), "2025-08", review)
    assert not any(f["type"] == "unbilled_overage" for f in findings)
    assert any("meter" in row["reason"] for row in review)


def test_ambiguous_minimum_and_seat_relationship_is_held_for_review():
    agreement = contract()
    for entry in agreement["minimum_schedule"]:
        entry["scope"] = "enterprise_workspace"
    agreement["confirmed"] = True
    review = []
    findings = reconcile(agreement, {"units": 260000}, invoice(), "2025-08", review)
    assert not any(f["type"] in {"underbilled_seats", "missing_base_charge"} for f in findings)
    assert any("relationship" in row["reason"] for row in review)
