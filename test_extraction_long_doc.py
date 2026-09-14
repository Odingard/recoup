from __future__ import annotations

from types import SimpleNamespace

import pytest

from recoup_agent.extraction.chunker import chunk_pages
from recoup_agent.extraction.eval.long_contract import build_long_contract
from recoup_agent.extraction.extractor import (
    ChunkExtraction,
    PageAnchoredEntitlement,
    _call,
    extract_pages,
)
from recoup_agent.extraction.ocr import _document_ai_pages
from recoup_agent.extraction.pages import DocumentTooLargeError, Page
from recoup_agent.extraction.verifier import verify
from recoup_agent.ingestion_doc import ContractEntitlements, Entitlement


class FakeResponse:
    def __init__(self, payload):
        self.text = payload


class FakeModels:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FakeClient:
    def __init__(self, responses):
        self.models = FakeModels(responses)
        self.caches = SimpleNamespace(created=[], deleted=[])

    def create_cache(self, **kwargs):
        cache = SimpleNamespace(name="cache/1")
        self.caches.created.append(kwargs)
        return cache


def _extract_payload(customer="Acme"):
    return '{"customer_name":"%s","entitlements":[]}' % customer


def test_chunking_has_markers_and_one_page_overlap():
    pages = [Page(i, "x" * 20) for i in range(1, 7)]
    chunks = chunk_pages(pages, max_chars=100, overlap_pages=1)
    assert "[[PAGE 1]]" in chunks[0].text
    assert chunks[1].pages[0].number == chunks[0].pages[-1].number


def test_long_contract_is_deterministic_and_contains_amendment_and_decoy():
    text1, expected1 = build_long_contract(7)
    text2, expected2 = build_long_contract(7)
    assert text1 == text2 and expected1 == expected2
    assert any("Amendment No. 1" in page for page in text1.split("\f"))
    assert "not a minimum commitment" in text1
    assert any(item.get("value") == 42000.0 or item.get("effective_date") == "2026-07-01" for item in expected1)


def test_dedupe_and_page_anchoring():
    payload = '{"customer_name":"Acme","entitlements":[' \
        '{"term_type":"committed_minimum","value":50000,"effective_date":"2026-01-01","confidence_score":0.9,"provenance":"minimum fee $50,000","page":2},' \
        '{"term_type":"committed_minimum","value":50000,"effective_date":"2026-01-01","confidence_score":0.9,"provenance":"minimum fee $50,000","page":2}]}'
    result = extract_pages([Page(1, "x"), Page(2, "minimum fee $50,000")], "pdf_text", client=FakeClient([FakeResponse(payload)]))
    assert len(result.entitlements) == 1
    assert result.entitlements[0].page == 2
    assert result.chunks == 1


def test_context_cache_path_deletes_cache_and_falls_back(monkeypatch):
    pages = [Page(i, "x" * 3000) for i in range(100)]
    payload = _extract_payload()
    client = FakeClient([FakeResponse(payload)] * 4)
    client.caches.create = client.create_cache
    client.caches.delete = lambda **kwargs: client.caches.deleted.append(kwargs)
    result = extract_pages(pages, "text", client=client)
    assert result.cached is True
    assert len(client.caches.created) == 1
    assert client.caches.deleted == [{"name": "cache/1"}]
    assert len(client.models.calls) == 4

    failing = FakeClient([FakeResponse(payload)] * 8)
    failing.caches.create = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("cache unavailable"))
    failing.caches.delete = lambda **kwargs: failing.caches.deleted.append(kwargs)
    result = extract_pages(pages, "text", client=failing)
    assert result.cached is False
    assert len(failing.models.calls) > 4


def test_verification_confidence_math_and_quote_search(monkeypatch):
    ent = PageAnchoredEntitlement(term_type="committed_minimum", value=50000, confidence_score=0.8,
                                  provenance="The minimum fee is $50,000.", page=1)
    payload = '{"results":[{"index":0,"verdict":"supports"}]}'
    verified = verify(SimpleNamespace(entitlements=[ent]), [Page(1, "The minimum fee is $50,000.")], client=FakeClient([FakeResponse(payload)]))
    assert verified[0].verification == {"quote_found": True, "page_matched": True, "model_check": "supports", "final_confidence": 0.8}

    missing = PageAnchoredEntitlement(term_type="committed_minimum", value=50000, confidence_score=0.9,
                                      provenance="not present", page=1)
    verified = verify(SimpleNamespace(entitlements=[missing]), [Page(1, "different text")], client=FakeClient([]))
    assert verified[0].verification["quote_found"] is False
    assert verified[0].confidence_score == 0.5


def test_retry_and_timeout_configuration(monkeypatch):
    class Transient:
        def __init__(self):
            self.calls = 0
        def generate_content(self, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("503 UNAVAILABLE")
            return FakeResponse(_extract_payload())

    monkeypatch.setattr("recoup_agent.extraction.extractor.time.sleep", lambda _: None)
    client = SimpleNamespace(models=Transient())
    response = _call(client, "gemini-2.5-pro", "prompt", object())
    assert response.text.startswith("{")
    assert client.models.calls == 3


def test_601_pages_raise_document_too_large(monkeypatch, tmp_path):
    from recoup_agent.extraction import pages as page_module

    class FakePdf:
        pages = [object()] * 601

    monkeypatch.setattr(page_module, "PdfReader", lambda path: FakePdf())
    with pytest.raises(DocumentTooLargeError) as exc:
        page_module.load_pages(str(tmp_path / "large.pdf"))
    assert exc.value.pages == 601


def test_legacy_mode(monkeypatch, tmp_path):
    import recoup_agent.ingestion_doc as module
    expected = ContractEntitlements(customer_name="Legacy", entitlements=[])
    monkeypatch.setenv("RECOUP_EXTRACTION_LEGACY", "1")
    monkeypatch.setattr(module, "_extract_single_shot", lambda path: expected)
    assert module.extract_entitlements(str(tmp_path / "contract.txt")) == expected


def test_document_ai_text_anchor_mapping():
    document = SimpleNamespace(
        text="first page second page",
        pages=[
            SimpleNamespace(layout=SimpleNamespace(text_anchor=SimpleNamespace(text_segments=[SimpleNamespace(start_index=0, end_index=10)]))),
            SimpleNamespace(layout=SimpleNamespace(text_anchor=SimpleNamespace(text_segments=[SimpleNamespace(start_index=11, end_index=22)]))),
        ],
    )
    assert [page.text for page in _document_ai_pages(document)] == ["first page", "second page"]
