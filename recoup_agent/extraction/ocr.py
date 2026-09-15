from __future__ import annotations

import json
import os
from typing import Protocol

from pydantic import BaseModel, Field

from .pages import Page

OCR_PROMPT = """Transcribe this document page by page. Return JSON: {\"pages\": [{\"number\": <1-based page number>, \"text\": \"<verbatim text of that page, preserving paragraph breaks and section numbers>\"}]}. Do not summarize, correct, or omit anything. If a page is blank, return an empty string for it."""


class OcrAdapter(Protocol):
    def page_texts(self, file_bytes: bytes, mime_type: str) -> list[Page]: ...


class OcrPage(BaseModel):
    number: int
    text: str = ""


class OcrPages(BaseModel):
    pages: list[OcrPage] = Field(default_factory=list)


def _ocr_client():
    from google import genai
    from google.genai import types
    timeout = int(os.getenv("RECOUP_EXTRACTION_TIMEOUT_MS", "120000"))
    return genai.Client(http_options=types.HttpOptions(timeout=timeout))


class GeminiOcr:
    def __init__(self, client=None, model: str | None = None):
        self.client = client or _ocr_client()
        self.model = model or os.getenv("RECOUP_OCR_MODEL", "gemini-2.5-flash")

    def page_texts(self, file_bytes: bytes, mime_type: str) -> list[Page]:
        from google.genai import types
        part = types.Part.from_bytes(data=file_bytes, mime_type=mime_type)
        response = self.client.models.generate_content(
            model=self.model,
            contents=[part, OCR_PROMPT],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=OcrPages,
                temperature=0.0),
        )
        parsed_value = getattr(response, "parsed", None)
        if parsed_value is not None:
            parsed = parsed_value if isinstance(parsed_value, OcrPages) else OcrPages.model_validate(parsed_value)
        else:
            raw = getattr(response, "text", response)
            parsed = OcrPages.model_validate_json(raw) if isinstance(raw, str) else OcrPages.model_validate(raw)
        return [Page(item.number or i + 1, item.text or "")
                for i, item in enumerate(parsed.pages)]


def _document_ai_pages(document) -> list[Page]:
    pages: list[Page] = []
    full_text = document.text or ""
    for number, page in enumerate(document.pages, 1):
        pieces = []
        for segment in page.layout.text_anchor.text_segments:
            start = int(segment.start_index or 0)
            end = int(segment.end_index or 0)
            pieces.append(full_text[start:end])
        pages.append(Page(number, "".join(pieces)))
    return pages


class DocumentAiOcr:
    def __init__(self, processor: str | None = None, client=None):
        self.processor = processor or os.environ["RECOUP_DOCAI_PROCESSOR"]
        self.client = client

    def page_texts(self, file_bytes: bytes, mime_type: str) -> list[Page]:
        from google.cloud import documentai_v1 as documentai
        client = self.client or documentai.DocumentProcessorServiceClient()
        request = documentai.ProcessRequest(
            name=self.processor,
            raw_document=documentai.RawDocument(content=file_bytes, mime_type=mime_type),
        )
        document = client.process_document(request=request).document
        return _document_ai_pages(document)


def get_ocr_adapter(*, client=None) -> OcrAdapter:
    processor = os.getenv("RECOUP_DOCAI_PROCESSOR")
    if processor:
        return DocumentAiOcr(processor, client=client)
    return GeminiOcr(client=client)
