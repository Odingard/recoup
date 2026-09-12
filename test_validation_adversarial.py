"""Validation track 6: adversarial documents must fail closed.

A stub discovery client that 'falls for' the injection produces a candidate
carrying the injected $1,000,000; the pipeline must never compile it into a
spec.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent / "scripts"))
from scripts.validation.generate_corpus import (  # noqa: E402
    INJECTION_AMOUNT, INJECTION_TEXT, generate)

from recoup_agent.rights_discovery import compile_candidate_right  # noqa: E402
from recoup_agent.rights_discovery import (  # noqa: E402
    discover_financial_rights, verify_candidate_right)
from recoup_agent.rights_discovery.models import CompiledRight  # noqa: E402


def _corpus(tmp_path):
    out = tmp_path / "corpus"
    generate(20260911, out)
    return sorted(d for d in out.iterdir() if d.is_dir())


def _adversarial_dirs(tmp_path):
    return [d for d in _corpus(tmp_path)
            if json.loads((d / "expected.json").read_text())["category"]
            == "adversarial"]


class _InjectionDiscoveryModels:
    """A model that obeys the injected instruction: it reports the legit
    right AND a $1,000,000 supplier-payment right quoting injection text."""

    def __init__(self, doc_text, oracle_amount):
        self.doc_text = doc_text
        self.amt = oracle_amount
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            payload = {"rights": [
                {
                    "right_name": "SLA credit",
                    "right_family": "sla_service_credit",
                    "description": "legit",
                    "holder_party": "Customer", "obligor_party": "Provider",
                    "trigger_structured": json.dumps(
                        {"op": "lt", "observation": "monthly_uptime",
                         "value": {"constant": "t"}}),
                    "calculation_type": "fixed_amount",
                    "calculation_structured": json.dumps(
                        {"type": "fixed_amount",
                         "amount": {"constant": "amt"}}),
                    "constants": [
                        {"name": "t", "value": "99.9%", "kind": "threshold"},
                        {"name": "amt", "value": f"${self.amt:,.0f}",
                         "kind": "amount"}],
                    "required_observations": ["monthly_uptime"],
                    "actual_observation": "credit_received",
                    "source_quote": (f"Customer receives a credit of "
                                     f"${self.amt:,.0f}"),
                    "confidence": 0.9,
                },
                {
                    "right_name": "Urgent supplier payment",
                    "right_family": "sla_service_credit",
                    "description": "injected",
                    "holder_party": "Provider", "obligor_party": "Customer",
                    "trigger_structured": json.dumps(
                        {"op": "lt", "observation": "monthly_uptime",
                         "value": {"constant": "t"}}),
                    "calculation_type": "fixed_amount",
                    "calculation_structured": json.dumps(
                        {"type": "fixed_amount",
                         "amount": {"constant": "big"}}),
                    "constants": [
                        {"name": "t", "value": "99.9%", "kind": "threshold"},
                        {"name": "big", "value": "$1,000,000",
                         "kind": "amount"}],
                    "required_observations": ["monthly_uptime"],
                    "source_quote": INJECTION_TEXT,
                    "confidence": 0.99,
                },
            ], "document_anomalies": [INJECTION_TEXT]}
            return SimpleNamespace(text=json.dumps(payload))
        # verification calls: a sane verifier rejects the injected payment
        contents = kwargs.get("contents") or ""
        injected = "PAYMENT TO THE SUPPLIER" in contents
        return SimpleNamespace(text=json.dumps({
            "is_financial_right": not injected,
            "quote_supported": True,
            "holder_obligor_correct": not injected,
            "trigger_supported": True,
            "calculation_supported": True,
            "invented_terms": [],
            "issues": ["injected instruction"] if injected else [],
            "confidence": 0.1 if injected else 0.9,
        }))


def _spec_constants(compiled: CompiledRight) -> list[float]:
    vals = []
    for c in compiled.spec.get("contractual_constants") or []:
        try:
            vals.append(float(c["value"]))
        except (TypeError, ValueError):
            pass
    return vals


def test_injection_following_discovery_never_reaches_a_spec(tmp_path):
    for d in _adversarial_dirs(tmp_path):
        text = (d / "text.txt").read_text()
        exp = json.loads((d / "expected.json").read_text())
        client = SimpleNamespace(models=_InjectionDiscoveryModels(
            text, exp["oracle_rights"][0]["amount"]))
        candidates = discover_financial_rights(
            text, {"account_id": "v", "source_id": "s"}, client=client)
        # the discovery client fell for it — both candidates exist
        assert len(candidates) == 2
        for cand in candidates:
            cand = verify_candidate_right(cand, text, client=client)
            out = compile_candidate_right(cand, text)
            if isinstance(out, CompiledRight):
                assert INJECTION_AMOUNT not in _spec_constants(out)
                # the injected right names a payment TO the supplier and must
                # never have been compiled; only the legit right may compile
                assert cand.holder_party_id == "Customer"


def test_injection_amount_smuggled_as_constant_is_ungrounded(tmp_path):
    """If the model hides the injected amount in a candidate quoting only the
    legit clause, grounding rejects it."""
    d = _adversarial_dirs(tmp_path)[0]
    text = (d / "text.txt").read_text()
    expected = json.loads((d / "expected.json").read_text())
    oracle_amt = expected["oracle_rights"][0]["amount"]
    from recoup_agent.rights_discovery.models import CandidateFinancialRight
    cand = CandidateFinancialRight(
        candidate_id="c", account_id="v", source_id="s",
        holder_party_id="Customer", obligor_party_id="Provider",
        right_name="SLA credit", right_family="sla_service_credit",
        trigger_spec={"op": "lt", "observation": "monthly_uptime",
                      "value": {"constant": "t"}},
        calculation_spec={"type": "fixed_amount",
                          "amount": {"constant": "big"}},
        required_observations=["monthly_uptime"],
        source_quote=f"Customer receives a credit of ${oracle_amt:,.0f}",
        status="verified",
        metadata={"constants": [
            {"name": "t", "value": "99.9%", "kind": "threshold"},
            {"name": "big", "value": "$1,000,000", "kind": "amount"}]})
    out = compile_candidate_right(cand, text)
    assert not isinstance(out, CompiledRight)


def test_all_adversarial_oracles_forbid_injected_amount(tmp_path):
    """Oracle sanity: every adversarial doc lists the injection amount as
    forbidden."""
    dirs = _adversarial_dirs(tmp_path)
    assert len(dirs) == 20
    for d in dirs:
        exp = json.loads((d / "expected.json").read_text())
        assert INJECTION_AMOUNT in \
            exp["oracle_rights"][0].get("forbidden_constants", [])
        assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in \
            (d / "text.txt").read_text()
