"""Run-005 defect remediation regression tests, grouped by R-id."""
from recoup_agent.identity import CustomerResolver
from recoup_agent.ingest_csv import load_invoices_csv, load_usage_csv
from recoup_agent.rights_discovery.compiler import (
    CompileFailure, compile_candidate_right)
from recoup_agent.rights_discovery.models import (
    CandidateFinancialRight, CompiledRight, RightSpec)
from recoup_agent.rights_discovery.runtime import evaluate_right


# ---------------------------------------------------------------------------
# R-01 — duplicate CSV rows must not double-count
# ---------------------------------------------------------------------------

_CONTRACTS = [
    {"customer_id": "acme", "customer_name": "Acme Corp"},
]


def test_r01_invoice_duplicates_counted_once(tmp_path):
    f = tmp_path / "invoices.csv"
    f.write_text(
        "customer,invoice_id,date,description,amount\n"
        "Acme Corp,INV-1,2026-06-05,Base subscription,1000.00\n"
        "Acme Corp,INV-1,2026-06-05,Tax,80.00\n"
        "Acme Corp,INV-1,2026-06-05,Tax,80.00\n"
        "Acme Corp,INV-1,2026-06-05,Base subscription,1000.00\n")
    invoices, nr = load_invoices_csv(f, CustomerResolver(_CONTRACTS))
    assert len(invoices) == 1
    inv = invoices[0]
    assert inv["amount_billed"] == 1080.00
    assert inv["tax_excluded"] == 80.00
    assert inv["base_charge"] == 1000.00
    dupes = [n for n in nr if n["term"] == "duplicate_row"]
    assert len(dupes) == 2
    assert "duplicates row" in dupes[0]["reason"]


def test_r01_usage_duplicate_counted_once(tmp_path):
    f = tmp_path / "usage.csv"
    f.write_text(
        "customer,month,metric,qty\n"
        "Acme Corp,2026-06,seats,500\n"
        "Acme Corp,2026-06,seats,500\n"
        "Acme Corp,2026-06,seats,700\n")
    usage, nr = load_usage_csv(f, CustomerResolver(_CONTRACTS))
    assert usage[0]["units"] == 1200
    assert any(n["term"] == "duplicate_row" for n in nr)


def test_r01_same_invoice_id_different_rows_not_duplicates(tmp_path):
    f = tmp_path / "invoices.csv"
    f.write_text(
        "customer,invoice_id,date,description,amount\n"
        "Acme Corp,INV-1,2026-06-05,Base subscription,1000.00\n"
        "Acme Corp,INV-1,2026-06-05,Overage charge,250.00\n")
    invoices, nr = load_invoices_csv(f, CustomerResolver(_CONTRACTS))
    assert len(invoices) == 1
    assert invoices[0]["amount_billed"] == 1250.00
    assert invoices[0]["overage_charge"] == 250.00
    assert not any(n["term"] == "duplicate_row" for n in nr)


def test_r01_template_formats_still_load():
    # Existing export-format coverage lives in test_templates.py /
    # test_uploaded_book.py / test_ingest_bulk.py — run together in CI; here
    # assert the resolver still accepts the template column spellings.
    from recoup_agent.ingest_csv import resolve_columns
    cols = resolve_columns(
        ["Customer", "InvoiceNumber", "Date", "LineAmount", "Item"],
        required=["customer", "amount"], optional=["period_start",
        "invoice_id", "description"], where="test")
    assert cols["amount"] == "LineAmount"


# ---------------------------------------------------------------------------
# R-02 — negative quantity guard must read quantity/value/amount
# ---------------------------------------------------------------------------

def _candidate_r02():
    return CandidateFinancialRight(
        candidate_id="c_r02", account_id="acct", source_id="src",
        holder_party_id="Buyer", obligor_party_id="Supplier",
        right_name="Metered surcharge", right_family="service_level_credit",
        trigger_spec={"op": "event_exists", "observation": "usage"},
        calculation_spec={"type": "per_unit",
                          "rate": {"constant": "rate"},
                          "quantity_observation": "usage"},
        required_observations=["usage"],
        source_quote="Buyer owes $0.50 per unit consumed.",
        status="verified",
        metadata={"constants": [{"name": "rate", "value": "$0.50",
                                 "kind": "rate"}]},
    )


def _spec_r02():
    quote = "Buyer owes $0.50 per unit consumed."
    out = compile_candidate_right(_candidate_r02(), quote)
    assert isinstance(out, CompiledRight), out
    return RightSpec.from_dict(out.spec)


def test_r02_negative_quantity_key():
    spec = _spec_r02()
    r = evaluate_right(spec, [{"type": "usage", "period": "2026-06",
                               "quantity": -5}], "2026-06")
    assert r.status == "not_evaluable"
    assert r.calculation_trace.get("reason") == "negative_quantity"
    assert r.expected_amount in (None, 0.0)


def test_r02_negative_amount_key():
    spec = _spec_r02()
    r = evaluate_right(spec, [{"type": "usage", "period": "2026-06",
                               "amount": -5}], "2026-06")
    assert r.status == "not_evaluable"
    assert r.calculation_trace.get("reason") == "negative_quantity"


def test_r02_positive_quantity_still_evaluates():
    spec = _spec_r02()
    r = evaluate_right(spec, [{"type": "usage", "period": "2026-06",
                               "quantity": 10}], "2026-06")
    assert r.status == "evaluated" and r.expected_amount == 5.00


# ---------------------------------------------------------------------------
# R-03 — exact id label unresolved when its key collides with a name key
# ---------------------------------------------------------------------------

_R03_CONTRACTS = [
    {"customer_id": "ACME-NORTH", "customer_name": "Acme North"},
    {"customer_id": "ACME", "customer_name": "Acme"},
    {"customer_id": "ACME-HOLD", "customer_name": "Acme Holdings"},
]


def test_r03_exact_id_and_name_resolve():
    r = CustomerResolver(_R03_CONTRACTS)
    assert r.resolve("ACME-NORTH") == "ACME-NORTH"
    assert r.resolve("acme north") == "ACME-NORTH"
    assert r.resolve("Acme") == "ACME"
    assert r.resolve("Acme LLC") == "ACME"


def test_r03_id_matches_name_of_other_contract():
    r = CustomerResolver(_R03_CONTRACTS + [
        {"customer_id": "ACME-LLC", "customer_name": "Acme LLC"}])
    # "Acme LLC" is an exact id AND exact name hit on the same contract.
    assert r.resolve("Acme LLC") == "ACME-LLC"
    assert r.resolve("Acme") == "ACME"


def test_r03_ambiguity_still_fails_closed():
    r = CustomerResolver([
        {"customer_id": "sdg", "customer_name": "Sterling Dental Group"},
        {"customer_id": "sdl", "customer_name": "Sterling Dental LLC"},
    ])
    assert r.resolve("Sterling Dental") is None
    assert "ambiguous" in r.explain("Sterling Dental")


# ---------------------------------------------------------------------------
# R-06 — unknown spec keys rejected
# ---------------------------------------------------------------------------

def _candidate_r06(**over):
    base = dict(
        candidate_id="c_r06", account_id="acct", source_id="src",
        holder_party_id="Buyer", obligor_party_id="Supplier",
        right_name="Late fee", right_family="service_level_credit",
        trigger_spec={"op": "event_exists", "observation": "invoice"},
        calculation_spec={"type": "fixed_amount",
                          "amount": {"constant": "fee"}},
        required_observations=["invoice"],
        source_quote="A late fee of $25 applies to any unpaid invoice.",
        status="verified",
        metadata={"constants": [{"name": "fee", "value": "$25",
                                 "kind": "amount"}]},
    )
    base.update(over)
    return CandidateFinancialRight(**base)


def test_r06_unknown_top_level_spec_key_rejected():
    out = compile_candidate_right({"spec_version": "1.0", "right_id": "x",
                                   "exec": "import os"}, "doc")
    assert isinstance(out, CompileFailure) and out.status == "rejected"
    assert any("unknown_spec_keys" in r for r in out.reasons)


def test_r06_unknown_trigger_node_key_rejected():
    cand = _candidate_r06(trigger_spec={
        "op": "event_exists", "observation": "invoice",
        "exec": "os.system('id')"})
    out = compile_candidate_right(cand, "doc")
    assert isinstance(out, CompileFailure) and out.status == "rejected"
    assert any("unknown_spec_keys" in r for r in out.reasons)


def test_r06_unknown_calc_key_rejected():
    cand = _candidate_r06(calculation_spec={
        "type": "fixed_amount", "amount": {"constant": "fee"},
        "exec": "os.system('id')"})
    out = compile_candidate_right(cand, "doc")
    assert isinstance(out, CompileFailure) and out.status == "rejected"


def test_r06_unknown_constant_key_rejected():
    cand = _candidate_r06(metadata={"constants": [
        {"name": "fee", "value": "$25", "kind": "amount",
         "exec": "os.system('id')"}]})
    out = compile_candidate_right(cand, "doc")
    assert isinstance(out, CompileFailure) and out.status == "rejected"


def test_r06_valid_spec_still_compiles():
    out = compile_candidate_right(_candidate_r06(), _candidate_r06().source_quote)
    assert isinstance(out, CompiledRight)
