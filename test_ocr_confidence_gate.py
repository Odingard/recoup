"""Document AI OCR confidence gate: block/token confidence flows into Page
objects and the verifier caps confidence for low-quality scans."""
from types import SimpleNamespace

import pytest

from recoup_agent.extraction.extractor import PageAnchoredEntitlement
from recoup_agent.extraction.ocr import (
    DocumentAiOcr, _docai_endpoint, _document_ai_pages)
from recoup_agent.extraction.pages import Page, TextBlock
from recoup_agent.extraction.verifier import verify


def _seg(start, end):
    return SimpleNamespace(start_index=start, end_index=end)


def _docai_document():
    full = ("Customer is licensed for 240 active user seats. "
            "The fee is $65.00 per seat.")
    seats_block = SimpleNamespace(layout=SimpleNamespace(
        confidence=0.62,
        text_anchor=SimpleNamespace(text_segments=[_seg(0, 48)])))
    fee_block = SimpleNamespace(layout=SimpleNamespace(
        confidence=0.97,
        text_anchor=SimpleNamespace(text_segments=[_seg(48, len(full))])))
    tokens = [SimpleNamespace(layout=SimpleNamespace(confidence=c))
              for c in (0.7, 0.8, 0.9)]
    page = SimpleNamespace(
        layout=SimpleNamespace(
            text_anchor=SimpleNamespace(text_segments=[_seg(0, len(full))])),
        blocks=[seats_block, fee_block], tokens=tokens)
    return SimpleNamespace(text=full, pages=[page])


def test_document_ai_pages_blocks_and_token_mean():
    pages = _document_ai_pages(_docai_document())
    assert len(pages) == 1
    page = pages[0]
    assert page.text.startswith("Customer is licensed")
    assert len(page.blocks) == 2
    assert page.blocks[0].confidence == 0.62
    assert page.blocks[0].text == "Customer is licensed for 240 active user seats. "
    assert page.ocr_confidence == pytest.approx(0.8)  # mean of 0.7/0.8/0.9


def test_docai_endpoint_derivation():
    assert _docai_endpoint("projects/p/locations/eu/processors/x") == \
        "eu-documentai.googleapis.com"
    assert _docai_endpoint("projects/p/locations/us/processors/x") == \
        "us-documentai.googleapis.com"


def test_documentai_ocr_with_injected_client_builds_no_real_client():
    adapter = DocumentAiOcr("projects/p/locations/eu/processors/x",
                            client=SimpleNamespace())
    assert adapter.processor.endswith("processors/x")


class _SupportsAllModels:
    def __init__(self):
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            text='{"results":[{"index":0,"verdict":"supports","reason":"ok"}]}')


class _SupportsAllClient:
    def __init__(self):
        self.models = _SupportsAllModels()


def _result(*ents):
    return SimpleNamespace(entitlements=list(ents))


def _ent(quote, conf=0.95, page=1):
    return PageAnchoredEntitlement(
        term_type="committed_seats", value=240.0, confidence_score=conf,
        provenance=quote, page=page)


_QUOTE = "Customer is licensed for 240 active user seats"


def test_low_confidence_block_caps_final_confidence():
    page = Page(1, _QUOTE + ".", blocks=(
        TextBlock(_QUOTE + ".", 0.62),
        TextBlock("The fee is $65.00 per seat.", 0.97)), ocr_confidence=0.9)
    out = verify(_result(_ent(_QUOTE)), [page], client=_SupportsAllClient())
    ent = out[0]
    assert ent.confidence_score <= 0.7
    assert ent.verification["ocr_gate"] is True
    assert ent.verification["ocr_confidence"] == 0.62


def test_high_confidence_block_leaves_score_unchanged():
    page = Page(1, _QUOTE + ".", blocks=(
        TextBlock(_QUOTE + ".", 0.97),), ocr_confidence=0.97)
    out = verify(_result(_ent(_QUOTE, 0.97)), [page], client=_SupportsAllClient())
    ent = out[0]
    assert ent.confidence_score == 0.97
    assert ent.verification["ocr_gate"] is False
    assert ent.verification["ocr_confidence"] == 0.97


def test_gate_fires_from_page_mean_when_no_block_overlaps():
    page = Page(1, _QUOTE + ".", blocks=(
        TextBlock("unrelated boilerplate clause text", 0.99),),
        ocr_confidence=0.80)
    out = verify(_result(_ent(_QUOTE)), [page], client=_SupportsAllClient())
    ent = out[0]
    assert ent.verification["ocr_gate"] is True
    assert ent.verification["ocr_confidence"] == 0.80
    assert ent.confidence_score <= 0.7


def test_gemini_pages_have_no_ocr_signal():
    page = Page(1, _QUOTE + ".")  # blocks=(), ocr_confidence=None
    out = verify(_result(_ent(_QUOTE, 0.97)), [page], client=_SupportsAllClient())
    ent = out[0]
    assert ent.verification["ocr_confidence"] is None
    assert ent.verification["ocr_gate"] is False
    assert ent.confidence_score == 0.97
