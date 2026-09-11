"""Rights Graph foundation tests — the graph must be deterministic,
provenance-linked, and never a second calculator (all amounts copied from
reconcile() findings or invoice/usage fields)."""
import re
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from recoup_agent import api
from recoup_agent.book_loader import load_book
from recoup_agent.billing import recoup_billing
from recoup_agent.pipeline import run_book
from recoup_agent.rights_graph import (
    B2BContractAdapter, RightsGraphService, RightStatus, stable_id,
)
from recoup_agent.rights_graph.service import annotate_findings_with_graph

GOLDEN = Path(__file__).parent / "golden"

ALLOWED_RIGHT_TYPES = {
    "committed_minimum", "usage_overage", "discount_expiration",
    "annual_escalator", "committed_seat_charge",
}


def _clean_graph(account_id="acct-test"):
    contracts, usage, invoices = load_book(GOLDEN / "clean")
    svc = RightsGraphService(account_id)
    return svc.build_for_book(contracts, usage, invoices), contracts, usage, invoices


def _strip_volatile(d):
    """Drop generated_at/ingestion_timestamp keys anywhere in a to_dict() tree."""
    if isinstance(d, dict):
        return {k: _strip_volatile(v) for k, v in d.items()
                if k not in ("generated_at", "ingestion_timestamp")}
    if isinstance(d, list):
        return [_strip_volatile(v) for v in d]
    return d


# A. expected right types across the clean book ------------------------------

def test_clean_book_right_types():
    graph, contracts, _, _ = _clean_graph()
    by_customer = {}
    for r in graph.rights:
        by_customer.setdefault(r.obligor_party_id, []).append(r)
        assert r.right_type in ALLOWED_RIGHT_TYPES
        assert r.holder_party_id == "acct-test"
        assert r.obligor_party_id == r.obligor_party_id
    assert any(r.right_type == "committed_minimum"
               for r in by_customer["cascade"])
    present = {r.right_type for r in graph.rights}
    assert {"committed_minimum", "usage_overage", "discount_expiration",
            "annual_escalator"} <= present
    assert len(graph.sources) >= len(contracts)


# B. every active right has resolvable quoted evidence -------------------------

def test_active_rights_have_quoted_evidence():
    graph, _, _, _ = _clean_graph()
    evidence_by_id = {e.evidence_id: e for e in graph.evidence}
    for r in graph.rights:
        if r.status != RightStatus.active.value:
            continue
        assert r.evidence_refs
        assert any(evidence_by_id[eid].quoted_text.strip()
                   for eid in r.evidence_refs)


# C. gating: low-confidence or unlinked rights fail closed ---------------------

def test_low_confidence_minimum_right_needs_review():
    contracts, usage, invoices = load_book(GOLDEN / "clean")
    target = next(c for c in contracts if c["customer_id"] == "cascade")
    contract = {**target, "term_meta": {
        **(target.get("term_meta") or {}),
        "committed_minimum_monthly": {"confidence": 0.5,
                                      "provenance": "weak quote"},
    }}
    u = next(u for u in usage if u["customer_id"] == "cascade")
    inv = next(i for i in invoices
               if i["customer_id"] == "cascade" and i["period"] == u["period"])
    svc = RightsGraphService("acct-test")
    graph = svc.build_for_book([contract], [u], [inv])
    minimum_rights = [r for r in graph.rights
                      if r.right_type == "committed_minimum"]
    assert minimum_rights
    assert all(r.status == RightStatus.inactive.value for r in minimum_rights)
    assert all(r.review_status == "needs_review" for r in minimum_rights)
    # findings gated identically -> no discrepancy may point at the right
    assert not [d for d in graph.discrepancies
                if d.right_id == minimum_rights[0].right_id]
    assert any(n.get("term") == "committed_minimum" for n in graph.needs_review)


def test_link_findings_skips_inactive_right():
    adapter = B2BContractAdapter()
    contract = {
        "customer_id": "weak", "customer_name": "Weak Co",
        "committed_minimum_monthly": 10000,
        "term_meta": {"committed_minimum_monthly": {"confidence": 0.5}},
        "clauses": {},
    }
    usage = {"customer_id": "weak", "period": "2026-06", "units": 1}
    invoice = {"customer_id": "weak", "period": "2026-06", "base_charge": 100}
    finding = {
        "finding_id": "F-WEAK-001", "customer_id": "weak", "customer_name": "Weak Co",
        "type": "unenforced_minimum", "title": "t", "monthly_recoverable": 9900.0,
        "clause_ref": "committed_minimum", "math": "m", "period": "2026-06",
        "confidence_score": 0.5,
    }
    nr = []
    graph = adapter.link_findings(contract, usage, invoice, "2026-06",
                                  "acct-test", [finding], needs_review=nr)
    assert graph.discrepancies == []
    assert "discrepancy_id" not in finding
    assert any("failed gating" in n["reason"] for n in nr)


# D. determinism + merge idempotency -------------------------------------------

def test_determinism_and_merge():
    g1, contracts, usage, invoices = _clean_graph()
    g2 = RightsGraphService("acct-test").build_for_book(contracts, usage, invoices)
    assert _strip_volatile(g1.to_dict()) == _strip_volatile(g2.to_dict())
    counts = {k: len(v) for k, v in g1.to_dict().items()}
    g1.merge(g2)
    merged = {k: len(v) for k, v in g1.to_dict().items()}
    assert merged == counts


# E. golden totals unchanged + discrepancy amounts mirror findings -------------

def test_golden_totals_unchanged_and_amounts_copied():
    contracts, usage, invoices = load_book(GOLDEN / "clean")
    findings_by_period, _ = run_book(contracts, usage, invoices)
    total = sum(f["monthly_recoverable"] for fs in findings_by_period.values() for f in fs)
    assert total == pytest.approx(11010.00, abs=0.01)

    # findings were annotated in-place by the pipeline hook
    all_findings = [f for fs in findings_by_period.values() for f in fs]
    assert all("discrepancy_id" in f for f in all_findings)

    graph, _, _, _ = _clean_graph()
    by_period_disc = {}
    for d in graph.discrepancies:
        exp = {e.expected_state_id: e for e in graph.expected_states}[d.expected_state_id]
        by_period_disc.setdefault(exp.period, 0.0)
        by_period_disc[exp.period] += d.recoverable_amount
    for period, fs in findings_by_period.items():
        fsum = round(sum(f["monthly_recoverable"] for f in fs), 2)
        assert round(by_period_disc.get(period, 0.0), 2) == pytest.approx(fsum, abs=0.01)
    # finding_ids reset per contract and repeat across periods -> key by both
    finding_amounts = {(f["finding_id"], f["period"]): f["monthly_recoverable"]
                       for f in all_findings}
    states = {e.expected_state_id: e for e in graph.expected_states}
    for d in graph.discrepancies:
        assert d.recoverable_amount == finding_amounts[
            (d.finding_id, states[d.expected_state_id].period)]


def test_messy_golden_cache_only(monkeypatch):
    import recoup_agent.ingest_dir as ingest_dir
    monkeypatch.setattr(ingest_dir, "extract_entitlements",
                        lambda p: (_ for _ in ()).throw(
                            AssertionError("extraction called; must be cache-only")))
    contracts, usage, invoices, seed = ingest_dir.load_book_from_dir(GOLDEN / "messy")
    findings_by_period, _ = run_book(contracts, usage, invoices, seed_review=seed)
    total = sum(f["monthly_recoverable"] for fs in findings_by_period.values() for f in fs)
    assert total == pytest.approx(11509.20, abs=0.01)


# F. no LLM imports in the package ----------------------------------------------

def test_no_llm_in_package():
    pkg = Path(__file__).parent / "recoup_agent" / "rights_graph"
    forbidden = re.compile(r"google\.genai|vertexai|ingestion_doc|from \.agent|import agent")
    for path in pkg.glob("*.py"):
        assert not forbidden.search(path.read_text()), path.name


# G. full trace chain -------------------------------------------------------------

def test_full_trace_chain():
    graph, _, _, _ = _clean_graph()
    states = {e.expected_state_id: e for e in graph.expected_states}
    rights = {r.right_id: r for r in graph.rights}
    sources = {s.source_id: s for s in graph.sources}
    evidence = {e.evidence_id: e for e in graph.evidence}
    obs = {o.observation_id: o for o in graph.observations}
    for d in graph.discrepancies:
        state = states[d.expected_state_id]
        right = rights[d.right_id]
        assert right.status == RightStatus.active.value
        assert state.right_id == right.right_id
        assert right.source_id in sources
        assert all(eid in evidence for eid in right.evidence_refs)
        assert all(oid in obs for oid in d.actual_observation_ids)
        trace = d.calculation_trace
        for key in ("rule", "inputs", "formula", "result", "currency"):
            assert key in trace
        assert trace["result"] == d.recoverable_amount


# H. tenant isolation --------------------------------------------------------------

def test_assert_tenant_raises_on_cross_account():
    graph, _, _, _ = _clean_graph(account_id="acct-B")
    with pytest.raises(PermissionError):
        RightsGraphService("acct-A").assert_tenant(graph)


def test_rights_endpoint_tenant_isolation(monkeypatch):
    monkeypatch.delenv("RECOUP_SAMPLE_MODE", raising=False)
    monkeypatch.setattr(recoup_billing, "is_configured", lambda: False)

    def fake_load_book(account_id):
        cid = "acme" if account_id == "acct-A" else "beta"
        return ([{"customer_id": cid, "customer_name": cid.title(),
                  "committed_minimum_monthly": 1000,
                  "clauses": {"committed_minimum": "Min $1000/mo"}}],
                [{"customer_id": cid, "period": "2026-06", "units": 1}],
                [{"customer_id": cid, "period": "2026-06", "base_charge": 100}])

    monkeypatch.setattr(api, "_load_book", fake_load_book)
    monkeypatch.setattr(api, "_findings_for", lambda _a: [])
    api.app.dependency_overrides[api.verify_token] = \
        lambda: {"uid": "acct-A", "account_id": "acct-A"}
    try:
        client = TestClient(api.app)
        assert client.get("/api/rights/customers/beta").status_code == 404
        resp = client.get("/api/rights/customers/acme")
        assert resp.status_code == 200
        body = resp.json()
        assert all(r["account_id"] == "acct-A" for r in body["rights"])
    finally:
        api.app.dependency_overrides.clear()


# I. stable ids -------------------------------------------------------------------

def test_stable_ids():
    adapter = B2BContractAdapter()
    contracts, usage, invoices = load_book(GOLDEN / "clean")
    c = contracts[0]
    key = ("cascade", "2026-06")
    u = next(u for u in usage if (u["customer_id"], u["period"]) == key)
    inv = next(i for i in invoices if (i["customer_id"], i["period"]) == key)
    _, g1 = adapter.evaluate(dict(c), u, inv, "2026-06", "acct-test")
    _, g2 = adapter.evaluate(dict(c), u, inv, "2026-06", "acct-test")
    inv2 = next(i for i in invoices
                if i["customer_id"] == "cascade" and i["period"] == "2026-07")
    u2 = next(u for u in usage
              if u["customer_id"] == "cascade" and u["period"] == "2026-07")
    _, g3 = adapter.evaluate(dict(c), u2, inv2, "2026-07", "acct-test")
    ids1 = [r.right_id for r in g1.rights]
    assert ids1 == [r.right_id for r in g2.rights]
    dsc1 = {d.discrepancy_id for d in g1.discrepancies}
    dsc3 = {d.discrepancy_id for d in g3.discrepancies}
    if dsc1 and dsc3:
        assert dsc1 != dsc3
        # same rights though: period only affects expected state/discrepancy ids
        assert {r.right_id for r in g3.rights} == set(ids1)


# J. sample mode -------------------------------------------------------------------

def test_sample_mode_rights_endpoint(monkeypatch):
    monkeypatch.setenv("RECOUP_SAMPLE_MODE", "1")
    client = TestClient(api.app)
    contracts, _, _ = load_book()
    cid = contracts[0]["customer_id"]
    resp = client.get(f"/api/rights/customers/{cid}")
    assert resp.status_code == 200
    body = resp.json()
    for key in ("sources", "rights", "observations", "expected_states",
                "discrepancies", "recovery_actions", "outcomes", "evidence",
                "needs_review"):
        assert key in body
    assert body["rights"]


# K. persistence bridge -------------------------------------------------------------

def test_save_findings_persists_graph_ids(monkeypatch):
    saved = []

    class FakeDocRef:
        def get(self):
            return types.SimpleNamespace(exists=False)

    class FakeBatch:
        def set(self, ref, data, merge=False):
            saved.append(data)

        def commit(self):
            pass

    class FakeAccount:
        def collection(self, _name):
            return self

        def document(self, _id):
            return FakeDocRef()

    class FakeClient:
        def collection(self, _name):
            return self

        def document(self, _id):
            return FakeAccount()

        def batch(self):
            return FakeBatch()

    monkeypatch.setattr(api.db, "get_client", lambda: FakeClient())
    api.db.save_findings("acct-1", [{
        "finding_id": "f1", "customer_id": "acme", "customer_name": "Acme",
        "type": "unenforced_minimum", "period": "2026-06",
        "monthly_recoverable": 500.0,
        "right_id": "rt_x", "expected_state_id": "exp_y",
        "discrepancy_id": "dsc_z",
    }])
    assert saved[0]["right_id"] == "rt_x"
    assert saved[0]["expected_state_id"] == "exp_y"
    assert saved[0]["discrepancy_id"] == "dsc_z"


def test_annotate_never_raises(monkeypatch):
    # malformed book -> annotation fails internally, findings pass through
    findings = [{"finding_id": "f", "customer_id": "x", "monthly_recoverable": 1.0}]
    out = annotate_findings_with_graph(findings, [{"customer_id": "x"}], [], [],
                                       "2026-06", "acct")
    assert out is findings or out == findings
