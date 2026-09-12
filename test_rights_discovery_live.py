"""Live Gemini smoke tests for rights discovery — skipped unless
GEMINI_API_KEY is set. These hit the real API; they are not part of CI.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("GEMINI_API_KEY"), reason="GEMINI_API_KEY not set")

from recoup_agent.rights_discovery import (  # noqa: E402
    compile_candidate_right, discover_financial_rights, evaluate_right,
    verify_candidate_right)
from recoup_agent.rights_discovery.eval.corpus import SCENARIOS  # noqa: E402
from recoup_agent.rights_discovery.models import CompiledRight, RightSpec  # noqa: E402

_DOC = {s["id"]: s for s in SCENARIOS}
CTX = {"account_id": "live-test", "source_id": "live-src"}


def _safe(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except Exception as exc:  # transient API errors (e.g. 503 high demand)
        pytest.skip(f"Gemini unavailable: {exc}")


def _pipeline(doc):
    out = []
    for cand in _safe(discover_financial_rights, doc, CTX):
        if cand.status == "discovered":
            cand = _safe(verify_candidate_right, cand, doc)
        if cand.status == "verified":
            result = compile_candidate_right(cand, doc)
            if isinstance(result, CompiledRight):
                out.append(result)
            else:
                cand.status = result.status
    return out


def test_live_sla_credit_end_to_end():
    compiled = _pipeline(_DOC["B"]["document"])
    if not compiled:
        pytest.skip("model produced no compiled right this run")
    spec = RightSpec.from_dict(compiled[0].spec)
    obs = [{"type": name, "value": 99.72, "period": "2026-06"}
           for name in spec.required_observations]
    if spec.actual_observation:
        obs.append({"type": spec.actual_observation, "amount": 0,
                    "period": "2026-06"})
    r = evaluate_right(spec, obs, "2026-06")
    assert r.status == "evaluated"
    assert r.recoverable_amount == 15000.00


def test_live_boilerplate_yields_no_candidates():
    cands = _safe(discover_financial_rights, _DOC["F"]["document"], CTX)
    assert cands == []


def test_live_legacy_only_routes_legacy():
    from recoup_agent.rights_discovery.compiler import route_family
    cands = _safe(discover_financial_rights, _DOC["A"]["document"], CTX)
    for c in cands:
        assert route_family(c.right_family, c.right_name) == "legacy"
