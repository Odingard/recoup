"""Compatibility exports for the document adapter interface."""
from importlib import import_module

from ..cloud.documents import (
    CloudDocumentAdapter as OcrAdapter,
    DocumentAiOcr,
    GeminiOcr,
    OCR_PROMPT,
    OcrPage,
    OcrPages,
    get_document_adapter as get_ocr_adapter,
)

__all__ = ["OcrAdapter", "DocumentAiOcr", "GeminiOcr", "OCR_PROMPT", "OcrPage", "OcrPages", "get_ocr_adapter"]


def _document_ai_pages(document):
    return import_module("recoup_agent.cloud.google_documents")._document_ai_pages(document)


def _docai_endpoint(processor_name):
    return import_module("recoup_agent.cloud.google_documents")._docai_endpoint(processor_name)
