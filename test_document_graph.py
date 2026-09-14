from __future__ import annotations

from recoup_agent.extraction.extractor import DocumentProfile, PageAnchoredEntitlement
from recoup_agent.extraction.graph import ExtractedDocument, assemble
from recoup_agent.ingest_bulk import ingest_files
from recoup_agent.ingestion_doc import ContractEntitlements, Entitlement
from recoup_agent.normalizer import normalize_contract_entitlements


def _ent(term_type, value, effective_date=None, **kw):
    return PageAnchoredEntitlement(term_type=term_type, value=value,
                                   effective_date=effective_date,
                                   confidence_score=0.95,
                                   provenance=kw.pop("provenance", "clause"),
                                   **kw)


def _doc(name, role, counterparty="Acme Corp", effective_date=None, entitlements=None,
         amendment_number=None, references=None, title=None):
    return ExtractedDocument(
        file_name=name,
        profile=DocumentProfile(role=role, counterparty=counterparty,
                                effective_date=effective_date, title=title,
                                references=references or [],
                                amendment_number=amendment_number),
        entitlements=entitlements or [], customer_name=counterparty)


def test_msa_amendment_exhibit_assemble_one_bundle():
    msa = _doc("msa.txt", "master", effective_date="2026-01-01", title="MSA",
               entitlements=[_ent("committed_minimum", 50000, "2026-01-01"),
                             _ent("term_start", 0, "2026-01-01")])
    amend = _doc("amendment-1.txt", "amendment", effective_date="2026-07-01",
                 amendment_number=1,
                 entitlements=[_ent("committed_minimum", 42000, "2026-07-01")])
    exhibit = _doc("exhibit-b.txt", "exhibit",
                   entitlements=[_ent("committed_seats", 125), _ent("seat_price", 120)])
    bundles = assemble([msa, amend, exhibit])
    assert len(bundles) == 1
    b = bundles[0]
    normalized = normalize_contract_entitlements(ContractEntitlements(
        customer_name=b.customer_name, entitlements=b.entitlements))
    assert len(normalized["minimum_schedule"]) == 2
    assert normalized["committed_seats"] == 125
    assert normalized["seat_price"] == 120
    assert {d["role"] for d in b.documents} == {"master", "amendment", "exhibit"}
    assert len(b.term_history["committed_minimum"]) == 2
    assert b.file_name == "msa.txt"
    min_ents = [e for e in b.entitlements if e.term_type == "committed_minimum"]
    assert {e.source_file for e in min_ents} == {"msa.txt", "amendment-1.txt"}


def test_exhibit_conflict_keeps_master_and_reviews():
    msa = _doc("msa.txt", "master", effective_date="2026-01-01",
               entitlements=[_ent("included_units", 10000)])
    exhibit = _doc("exhibit.txt", "exhibit",
                   entitlements=[_ent("included_units", 20000)])
    (b,) = assemble([msa, exhibit])
    units = [e for e in b.entitlements if e.term_type == "included_units"]
    assert [e.value for e in units] == [10000]
    assert any("conflicts with master" in n["reason"] for n in b.graph_needs_review)


def test_undated_amendment_not_applied():
    msa = _doc("msa.txt", "master", effective_date="2026-01-01",
               entitlements=[_ent("overage_rate", 3.5)])
    amend = _doc("amend.txt", "amendment", amendment_number=2,
                 entitlements=[_ent("overage_rate", 2.0)])
    (b,) = assemble([msa, amend])
    rates = [e.value for e in b.entitlements if e.term_type == "overage_rate"]
    assert rates == [3.5]
    assert any("no effective date" in n["reason"] for n in b.graph_needs_review)


def test_two_masters_latest_wins():
    old = _doc("msa-old.txt", "master", effective_date="2024-01-01",
               entitlements=[_ent("committed_minimum", 30000, "2024-01-01")])
    new = _doc("msa-new.txt", "master", effective_date="2026-01-01",
               entitlements=[_ent("committed_minimum", 50000, "2026-01-01")])
    (b,) = assemble([old, new])
    assert b.file_name == "msa-new.txt"
    assert any("superseded" in n["reason"] for n in b.graph_needs_review)


def test_different_counterparty_own_group():
    msa = _doc("msa.txt", "master", counterparty="Acme Corp")
    amend = _doc("amend.txt", "amendment", counterparty="Globex",
                 effective_date="2026-07-01", entitlements=[_ent("overage_rate", 2.0)])
    bundles = assemble([msa, amend])
    assert len(bundles) == 2


def test_ingest_merges_amendment_into_existing_contract():
    prior = {
        "customer_id": "acme_corp", "customer_name": "Acme Corp",
        "file_name": "msa.txt",
        "source_entitlements": [
            Entitlement(term_type="committed_minimum", value=50000,
                        effective_date="2026-01-01", confidence_score=0.95,
                        provenance="minimum", source_file="msa.txt").model_dump()],
        "documents": [{"file_name": "msa.txt", "role": "master",
                       "effective_date": "2026-01-01"}],
    }

    def extract(path):
        return ContractEntitlements(
            customer_name="Acme Corp",
            document={"role": "amendment", "counterparty": "Acme Corp",
                      "effective_date": "2026-07-01", "amendment_number": 1},
            entitlements=[Entitlement(term_type="committed_minimum", value=42000,
                                      effective_date="2026-07-01",
                                      confidence_score=0.9, provenance="amend")])

    result = ingest_files([("amendment-1.txt", b"amendment text")], [prior], extract)
    assert len(result.contracts) == 1
    merged = result.contracts[0]
    assert merged["customer_id"] == "acme_corp"
    assert len(merged["minimum_schedule"]) == 2
    assert len(merged["documents"]) == 2

    result2 = ingest_files([("amendment-1.txt", b"amendment text")],
                           [{"customer_id": "acme_corp", "customer_name": "Acme Corp"}],
                           extract)
    assert not result2.contracts
    assert any("Re-upload the master" in n["reason"] for n in result2.needs_review)


def test_filename_role_override_beats_model():
    from recoup_agent.extraction.extractor import _override_role
    assert _override_role("master", "Amendment_No_2.pdf", "") == "amendment"
    assert _override_role("amendment", "", "Master Subscription Agreement body") in {"master", "amendment"}
    assert _override_role("other", "order_form.pdf", "") == "order_form"
    assert _override_role("other", "", "Exhibit B — Pricing") == "exhibit"


def test_bundle_generator_assembles_same_terms():
    from recoup_agent.extraction.eval.long_contract import build_long_contract_bundle
    docs, expected = build_long_contract_bundle(7)
    assert len(docs) == 3
    amendments = [e for e in expected if e.get("value") == 42000.0]
    seats = [e for e in expected if e.get("term_type") == "committed_seats"]
    assert amendments and seats
