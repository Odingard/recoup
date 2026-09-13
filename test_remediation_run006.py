"""Run-006 defect remediation regression tests, grouped by defect id."""
from recoup_agent.identity import CustomerResolver
from recoup_agent.rights_discovery.compiler import (
    CompileFailure, compile_candidate_right)
from recoup_agent.rights_discovery.models import (
    CandidateFinancialRight, CompiledRight)


def _candidate(trigger, calc, constants, quote, name="Some right",
               family="service_level_credit"):
    return CandidateFinancialRight(
        candidate_id="c_n01", account_id="acct", source_id="src",
        holder_party_id="Buyer", obligor_party_id="Supplier",
        right_name=name, right_family=family,
        trigger_spec=trigger, calculation_spec=calc,
        required_observations=["invoice_total"],
        source_quote=quote, status="verified",
        metadata={"constants": constants},
    )


# ---------------------------------------------------------------------------
# N-01 — minuend/subtrahend accept observation refs
# ---------------------------------------------------------------------------

_QUOTE = "The credit equals the invoiced total minus the contractual cap of $500."


def test_n01_difference_constant_minuend_observation_subtrahend():
    cand = _candidate(
        {"op": "event_exists", "observation": "invoice_total"},
        {"type": "difference", "minuend": {"constant": "cap_amt"},
         "subtrahend": {"observation": "invoice_total"}},
        [{"name": "cap_amt", "value": "$500", "kind": "amount"}],
        _QUOTE)
    out = compile_candidate_right(cand, _QUOTE)
    assert isinstance(out, CompiledRight), out


def test_n01_difference_observation_minuend_constant_subtrahend():
    cand = _candidate(
        {"op": "event_exists", "observation": "invoice_total"},
        {"type": "difference", "minuend": {"observation": "invoice_total"},
         "subtrahend": {"constant": "cap_amt"}},
        [{"name": "cap_amt", "value": "$500", "kind": "amount"}],
        _QUOTE)
    out = compile_candidate_right(cand, _QUOTE)
    assert isinstance(out, CompiledRight), out


def test_n01_subtrahend_extra_key_still_rejected():
    cand = _candidate(
        {"op": "event_exists", "observation": "invoice_total"},
        {"type": "difference", "minuend": {"constant": "cap_amt"},
         "subtrahend": {"observation": "invoice_total", "extra": 1}},
        [{"name": "cap_amt", "value": "$500", "kind": "amount"}],
        _QUOTE)
    out = compile_candidate_right(cand, _QUOTE)
    assert isinstance(out, CompileFailure) and out.status == "rejected"
    assert any("unknown_spec_keys" in r for r in out.reasons)


def test_n01_every_calculation_type_compiles():
    """Positive-control sweep: one minimal valid candidate per grammar type."""
    q = ("Buyer pays $0.50 per unit; monthly fee is $500 at 5% for tiers "
         "of 100 units; late fee applies after 2026-03-31.")
    cases = [
        {"type": "fixed_amount", "amount": {"constant": "fee"}},
        {"type": "percentage_of", "rate": {"constant": "rate"},
         "base_observation": "invoice_total"},
        {"type": "per_unit", "rate": {"constant": "rate"},
         "quantity_observation": "invoice_total"},
        {"type": "difference", "minuend": {"constant": "fee"},
         "subtrahend": {"observation": "invoice_total"}},
        {"type": "tiered", "quantity_observation": "invoice_total",
         "tiers": [{"up_to": {"constant": "t1"}, "rate": {"constant": "rate"}},
                   {"up_to": None, "rate": {"constant": "rate"}}]},
        {"type": "volume_tiered", "quantity_observation": "invoice_total",
         "tiers": [{"up_to": {"constant": "t1"}, "rate": {"constant": "rate"}},
                   {"up_to": None, "rate": {"constant": "rate"}}]},
        {"type": "min_of",
         "operands": [{"constant": "fee"},
                      {"observation": "invoice_total"}]},
        {"type": "max_of",
         "operands": [{"constant": "fee"},
                      {"observation": "invoice_total"}]},
        {"type": "banded_percentage_of", "base_observation": "invoice_total",
         "band_observation": "invoice_total",
         "bands": [{"up_to": {"constant": "t1"}, "rate": {"constant": "rate"}},
                   {"up_to": None, "rate": {"constant": "rate2"}}]},
        {"type": "fixed_amount", "amount": {"constant": "fee"},
         "cap": {"constant": "cap_amt"}, "floor": {"constant": "floor_amt"}},
    ]
    constants = [
        {"name": "fee", "value": "$500", "kind": "amount"},
        {"name": "rate", "value": "5%", "kind": "percentage"},
        {"name": "rate2", "value": "$0.50", "kind": "rate"},
        {"name": "t1", "value": "100", "kind": "quantity"},
        {"name": "cap_amt", "value": "$500", "kind": "amount"},
        {"name": "floor_amt", "value": "$500", "kind": "amount"},
    ]
    for calc in cases:
        cand = _candidate(
            {"op": "event_exists", "observation": "invoice_total"},
            calc, constants, q, name=f"Sweep {calc['type']}")
        out = compile_candidate_right(cand, q)
        assert isinstance(out, CompiledRight), (calc["type"], out)


# ---------------------------------------------------------------------------
# R-03 / ID-004 — verbatim tier: same normalized key, different punctuation
# ---------------------------------------------------------------------------

def test_id004_verbatim_id_vs_name():
    r = CustomerResolver([
        {"customer_id": "CUST-005", "customer_name": "Acme North"},
        {"customer_id": "CUST-007", "customer_name": "ACME-NORTH"},
    ])
    assert r.resolve("ACME-NORTH") == "CUST-007"
    assert r.resolve("Acme North") == "CUST-005"
    assert r.resolve("acme north") == "CUST-005"
    assert r.resolve("acme-north") == "CUST-007"
    assert "matched" in r.explain("ACME-NORTH")


def test_id004_ambiguity_still_fails_closed():
    r = CustomerResolver([
        {"customer_id": "sdg", "customer_name": "Sterling Dental Group"},
        {"customer_id": "sdl", "customer_name": "Sterling Dental LLC"},
    ])
    assert r.resolve("Sterling Dental") is None
    assert r.explain("Sterling Dental") == \
        "ambiguous: 'Sterling Dental' matches sdg, sdl"
    assert r.resolve("sterling") is None


def test_id004_run005_acme_cases_unchanged():
    contracts = [
        {"customer_id": "ACME-NORTH", "customer_name": "Acme North"},
        {"customer_id": "ACME", "customer_name": "Acme"},
        {"customer_id": "ACME-HOLD", "customer_name": "Acme Holdings"},
    ]
    r = CustomerResolver(contracts)
    assert r.resolve("ACME-NORTH") == "ACME-NORTH"
    assert r.resolve("acme north") == "ACME-NORTH"
    assert r.resolve("Acme") == "ACME"
    assert r.resolve("Acme LLC") == "ACME"
    r2 = CustomerResolver(contracts + [
        {"customer_id": "ACME-LLC", "customer_name": "Acme LLC"}])
    assert r2.resolve("Acme LLC") == "ACME-LLC"
    assert r2.resolve("Acme") == "ACME"
