from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol

from pydantic import BaseModel, Field

from .models import BinaryPart, GenerationConfig, ModelResponse, ProviderConfigurationError, get_model_adapter


@dataclass(frozen=True)
class TextBlock:
    text: str
    confidence: float


@dataclass(frozen=True)
class Page:
    number: int
    text: str
    blocks: tuple[TextBlock, ...] = ()
    ocr_confidence: float | None = None


class CloudDocumentAdapter(Protocol):
    def page_texts(self, file_bytes: bytes, mime_type: str) -> list[Page]: ...


class OcrPage(BaseModel):
    number: int
    text: str = ""


class OcrPages(BaseModel):
    pages: list[OcrPage] = Field(default_factory=list)


OCR_PROMPT = """Transcribe this document page by page. Return JSON: {"pages": [{"number": <1-based page number>, "text": "<verbatim text of that page, preserving paragraph breaks and section numbers>"}]}. Do not summarize, correct, or omit anything. If a page is blank, return an empty string for it."""


class GeminiOcr:
    def __init__(self, client=None, model: str | None = None):
        self.client = get_model_adapter(client)
        self.model = model or os.getenv("RECOUP_OCR_MODEL", "gemini-2.5-flash")

    def page_texts(self, file_bytes: bytes, mime_type: str) -> list[Page]:
        response: ModelResponse = self.client.generate(
            model=self.model, contents=[BinaryPart(file_bytes, mime_type), OCR_PROMPT],
            config=GenerationConfig(response_schema=OcrPages))
        parsed = (OcrPages.model_validate(response.parsed) if response.parsed is not None
                  else OcrPages.model_validate_json(response.text))
        return [Page(item.number or i + 1, item.text or "")
                for i, item in enumerate(parsed.pages)]


class DocumentAiOcr:
    def __init__(self, processor: str | None = None, client=None):
        self.processor = processor or os.environ["RECOUP_DOCAI_PROCESSOR"]
        self.client = client

    def page_texts(self, file_bytes: bytes, mime_type: str) -> list[Page]:
        provider = import_module(".google_documents", __package__)
        return provider.process_document(self.processor, file_bytes, mime_type, self.client)


def get_document_adapter(*, client=None) -> CloudDocumentAdapter:
    processor = os.getenv("RECOUP_DOCAI_PROCESSOR", "").strip()
    provider = os.getenv("RECOUP_OCR_PROVIDER", "auto").strip().lower()
    if provider == "auto":
        provider = "documentai" if processor else "local"
    if provider == "documentai":
        if not processor:
            raise ProviderConfigurationError("RECOUP_DOCAI_PROCESSOR is required for Document AI.")
        return DocumentAiOcr(processor, client=client)
    if provider == "gemini":
        return GeminiOcr(client=client)
    if provider == "local":
        return import_module(".local_documents", __package__).LocalDocumentAdapter()
    raise ProviderConfigurationError(f"Unsupported RECOUP_OCR_PROVIDER: {provider}")
