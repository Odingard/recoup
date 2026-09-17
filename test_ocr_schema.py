"""Category-4 OCR regression: typed page schema + fail-closed empty OCR."""
import subprocess
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from reportlab.pdfgen import canvas

from recoup_agent.cloud.local_documents import pdf_text_pages
from recoup_agent.extraction.ocr import GeminiOcr, OcrPage, OcrPages
from recoup_agent.extraction.pages import Page
from recoup_agent.ingest_bulk import ingest_files
from recoup_agent.ingestion_doc import (
    ContractEntitlements,
    UnreadableDocumentError,
    extract_entitlements,
)


class _FakeModels:
    def __init__(self, response):
        self._response = response

    def generate_content(self, **kwargs):
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.models = _FakeModels(response)


def test_gemini_ocr_typed_pages_schema_and_parsed_pages():
    schema = OcrPages.model_json_schema()
    items = schema["properties"]["pages"]["items"]
    if "$ref" in items:
        items = schema["$defs"][items["$ref"].rsplit("/", 1)[-1]]
    assert items["properties"]["text"]["type"] == "string"
    assert items["properties"]["number"]["type"] == "integer"

    response = SimpleNamespace(parsed=OcrPages(pages=[
        OcrPage(number=1, text="A"), OcrPage(number=2, text="")]))
    ocr = GeminiOcr(client=_FakeClient(response))
    pages = ocr.page_texts(b"pdf-bytes", "application/pdf")
    assert [(p.number, p.text) for p in pages] == [(1, "A"), (2, "")]


def test_extract_entitlements_fails_closed_on_empty_ocr(monkeypatch, tmp_path):
    from recoup_agent.extraction import pages as page_module
    from recoup_agent.extraction import ocr as ocr_module

    monkeypatch.setattr(page_module, "load_pages",
                        lambda path: ([Page(1, ""), Page(2, "")], "scanned"))
    monkeypatch.setattr(ocr_module, "get_ocr_adapter",
                        lambda **kw: SimpleNamespace(
                            page_texts=lambda *a, **k: [Page(1, ""), Page(2, "")]))

    pdf = tmp_path / "scanned.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake scanned")
    with pytest.raises(UnreadableDocumentError):
        extract_entitlements(str(pdf))


class _PipelineModels:
    """Fake client covering classify → extract → verify."""

    def generate_content(self, **kwargs):
        config = kwargs.get("config")
        schema = getattr(config, "response_schema", None)
        if getattr(schema, "__name__", "") == "DocumentProfile":
            return SimpleNamespace(
                text='{"role":"master","title":"License",'
                     '"counterparty":"Redwood Field Services, LLC"}')
        if getattr(schema, "__name__", "") == "VerificationBatch":
            return SimpleNamespace(
                text='{"results":[{"index":0,"verdict":"supports","reason":"ok"}]}')
        return SimpleNamespace(
            text='{"customer_name":"Redwood Field Services, LLC","entitlements":['
                 '{"term_type":"committed_seats","value":240,"effective_date":null,'
                 '"confidence_score":0.95,"provenance":"240 seats","page":1}]}')


def test_extract_entitlements_with_docai_blocks(monkeypatch, tmp_path):
    """extract_entitlements works end-to-end when OCR pages carry blocks."""
    from recoup_agent.extraction import ocr as ocr_module

    text = "Customer commits to 240 seats at $65 per seat."
    source = tmp_path / "source.pdf"
    document = canvas.Canvas(str(source), pagesize=(612, 792))
    document.setFont("Helvetica", 18)
    document.drawString(50, 700, text)
    document.save()
    page = pdf_text_pages(str(source))[0]
    ocr_page = replace(page, ocr_confidence=0.97, blocks=tuple(
        replace(block, confidence=0.97) for block in page.blocks))
    prefix = tmp_path / "scan"
    subprocess.run(
        ["pdftoppm", "-r", "150", "-png", "-singlefile", str(source), str(prefix)],
        check=True, capture_output=True, timeout=120)
    pdf = tmp_path / "scanned.pdf"
    document = canvas.Canvas(str(pdf), pagesize=(612, 792))
    document.drawImage(str(prefix.with_suffix(".png")), 0, 0, width=612, height=792)
    document.save()
    page_texts = Mock(return_value=[ocr_page])
    monkeypatch.setattr(ocr_module, "get_ocr_adapter",
                        lambda **kw: SimpleNamespace(
                            page_texts=page_texts))

    result = extract_entitlements(
        str(pdf), client=SimpleNamespace(models=_PipelineModels()))
    assert result.customer_name == "Redwood Field Services, LLC"
    assert result.entitlements[0].term_type == "committed_seats"
    assert result.entitlements[0].verification["ocr_confidence"] == 0.97
    assert result.structural_verification["state"] == "Verified"
    page_texts.assert_called_once()


def test_bulk_unknown_customer_empty_extraction_is_error():
    result = ingest_files(
        [("scanned.pdf", b"%PDF-1.4")], [],
        lambda path: ContractEntitlements(customer_name="Unknown",
                                        entitlements=[]))
    assert result.contracts == []
    row = result.files[0]
    assert row["status"] == "error"
    assert row["message"] == "No terms could be extracted."
    review = [r for r in result.needs_review if r["term"] == "contract_extraction"]
    assert len(review) == 1
    assert "unreadable or is not an agreement" in review[0]["reason"]
