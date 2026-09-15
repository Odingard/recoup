"""Category-4 OCR regression: typed page schema + fail-closed empty OCR."""
from types import SimpleNamespace

import pytest

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
