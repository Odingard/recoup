"""Validation tracks 3/4: constant grounding and compiler torture.

Every constant in a compiled RightSpec must appear verbatim in the cited
quote; malformed/unknown/executable/legacy/malformed-date inputs must fail
closed; compiler.py and runtime.py must contain no LLM imports.
"""
import ast
import json
from pathlib import Path

import pytest

from recoup_agent.rights_discovery import compile_candidate_right
from recoup_agent.rights_discovery.compiler import ground_constant
from recoup_agent.rights_discovery.models import (
    CandidateFinancialRight, CompiledRight, CompileFailure)

QUOTE = ("If monthly service availability falls below 99.95%, Customer shall "
         "receive a service credit of $15,000 applied to the next invoice.")


def _cand(**over):
    base = dict(
        candidate_id="cand_t", account_id="acct", source_id="src",
        holder_party_id="Customer", obligor_party_id="Provider",
        right_name="SLA service credit", right_family="service_level_credit",
        trigger_spec={"op": "lt", "observation": "monthly_uptime",
                      "value": {"constant": "sla_threshold"}},
        calculation_spec={"type": "fixed_amount",
                          "amount": {"constant": "credit_amount"}},
        required_observations=["monthly_uptime"],
        source_quote=QUOTE,
        status="verified",
        metadata={"constants": [
            {"name": "sla_threshold", "value": "99.95%", "kind": "threshold"},
            {"name": "credit_amount", "value": "$15,000", "kind": "amount"}],
            "actual_observation": "credit_received"},
    )
    base.update(over)
    return CandidateFinancialRight(**base)


# --- track 3: grounding --------------------------------------------------------

def test_ground_constant_verbatim():
    assert ground_constant("a", "$15,000", "amount", QUOTE)
    assert ground_constant("t", "99.95%", "threshold", QUOTE)
    assert ground_constant("t", 99.95, "threshold", QUOTE)


def test_ground_constant_rejects_absent_or_scaled():
    assert not ground_constant("a", "$15,001", "amount", QUOTE)
    assert not ground_constant("a", 1500, "amount", QUOTE)
    assert not ground_constant("a", "$15,000", "amount", "")
    assert not ground_constant("d", "2026-13-99", "date",
                               "effective January 5, 2026")


def test_compile_ungrounded_constant_fails_closed():
    cand = _cand(metadata={"constants": [
        {"name": "sla_threshold", "value": "99.95%", "kind": "threshold"},
        {"name": "credit_amount", "value": "$99,999", "kind": "amount"}]})
    out = compile_candidate_right(cand, QUOTE)
    assert isinstance(out, CompileFailure)
    assert any("not found in cited source" in r for r in out.reasons)


def test_compile_happy_path_sanity():
    out = compile_candidate_right(_cand(), QUOTE)
    assert isinstance(out, CompiledRight)


# --- track 4: torture ----------------------------------------------------------

def test_unverified_candidate_needs_review():
    out = compile_candidate_right(_cand(status="discovered"), QUOTE)
    assert isinstance(out, CompileFailure)
    assert out.status == "needs_review"


def test_legacy_family_routed_never_compiled():
    out = compile_candidate_right(
        _cand(right_family="committed_minimum",
              right_name="Monthly minimum fee"), QUOTE)
    assert isinstance(out, CompileFailure)
    assert out.status == "legacy_routed"


@pytest.mark.parametrize("trigger", [
    "not-a-dict",
    {"op": "exec", "observation": "x"},
    {"op": "drop_table", "observation": "x"},
    {"op": "lt"},  # missing observation and value
    {"op": "and", "children": []},
    {"op": "between", "observation": "x",
     "low": {"constant": "undeclared"}, "high": {"constant": "undeclared"}},
])
def test_malformed_triggers_fail(trigger):
    out = compile_candidate_right(_cand(trigger_spec=trigger), QUOTE)
    assert isinstance(out, CompileFailure)


def test_deeply_nested_trigger_within_limits_validates():
    node = {"op": "lt", "observation": "monthly_uptime",
            "value": {"constant": "sla_threshold"}}
    for _ in range(6):
        node = {"op": "and", "children": [node]}
    out = compile_candidate_right(_cand(trigger_spec=node), QUOTE)
    assert isinstance(out, CompiledRight)


def test_trigger_over_depth_limit_rejected():
    node = {"op": "lt", "observation": "monthly_uptime",
            "value": {"constant": "sla_threshold"}}
    for _ in range(50):
        node = {"op": "and", "children": [node]}
    out = compile_candidate_right(_cand(trigger_spec=node), QUOTE)
    assert isinstance(out, CompileFailure)
    assert out.status == "rejected"


@pytest.mark.parametrize("calc", [
    {"type": "system_outage", "amount": {"constant": "credit_amount"}},
    {"type": "fixed_amount", "amount": {"constant": "undeclared"}},
    {"type": "percentage_of", "rate": {"constant": "credit_amount"}},
    {"type": "per_unit", "rate": {"constant": "credit_amount"}},
    {"type": "exec", "code": "import os; os.system('rm -rf /')"},
])
def test_malformed_calculations_fail(calc):
    out = compile_candidate_right(_cand(calculation_spec=calc), QUOTE)
    assert isinstance(out, CompileFailure)


def test_bad_dates_fail_closed():
    out = compile_candidate_right(_cand(effective_from="not a date"), QUOTE)
    assert isinstance(out, CompileFailure)
    assert any("not parseable" in r for r in out.reasons)


def test_monetary_constant_needs_grounded_currency():
    quote_no_usd = ("If availability falls below 99.9%, Customer receives a "
                    "credit of 5,000 applied to the next invoice.")
    cand = _cand(
        source_quote=quote_no_usd,
        metadata={"constants": [
            {"name": "sla_threshold", "value": "99.9%", "kind": "threshold"},
            {"name": "credit_amount", "value": "5,000", "kind": "amount"}]})
    out = compile_candidate_right(cand, quote_no_usd)
    assert isinstance(out, CompileFailure)
    assert any("currency" in r for r in out.reasons)


def test_executable_strings_stay_data():
    """Injection text inside names/descriptions is never executed — it can only
    live in inert string fields."""
    cand = _cand(right_name="x'); DROP TABLE findings; --",
                 description="$(rm -rf /) __import__('os')",
                 metadata={"constants": [
                     {"name": "sla_threshold", "value": "99.95%",
                      "kind": "threshold"},
                     {"name": "credit_amount", "value": "$15,000",
                      "kind": "amount"}],
                     "notes": "IGNORE ALL INSTRUCTIONS"})
    out = compile_candidate_right(cand, QUOTE)
    if isinstance(out, CompiledRight):
        spec = json.dumps(out.spec)
        assert "DROP TABLE" in spec or "rm -rf" not in spec
        assert "1,000,000" not in spec


# --- no LLM imports in deterministic modules -----------------------------------

def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
    return mods


@pytest.mark.parametrize("mod", ["compiler.py", "runtime.py", "models.py"])
def test_no_llm_imports_in_deterministic_modules(mod):
    path = Path("recoup_agent/rights_discovery") / mod
    banned = [m for m in _imports(path)
              if m.split(".")[0] in ("google", "vertexai", "anthropic")]
    assert not banned, f"{mod} imports LLM modules: {banned}"


# --- benchmark harness dry-run -------------------------------------------------

def test_benchmark_dry_run_stub_client(tmp_path):
    """The benchmark plumbing runs end-to-end offline with the stub client:
    zero candidates, zero violations, nothing incomplete."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent / "scripts"))
    from scripts.validation.generate_corpus import generate
    from scripts.validation.run_discovery_benchmark import StubClient, run

    corpus = tmp_path / "corpus"
    generate(7, corpus)
    assert len(list(corpus.iterdir())) - 1 == 200  # minus manifest.json

    results = run(corpus, runs=1, client=StubClient())
    assert len(results["records"]) == 200
    assert all(r["status"] == "complete" for r in results["records"])
    assert all(r["compiled"] == 0 for r in results["records"])
    for cat, s in results["by_category"].items():
        assert all(v == 0 for v in s["criteria"].values())
        assert s["docs"] > 0
