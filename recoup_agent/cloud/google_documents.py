from __future__ import annotations

from google.api_core.client_options import ClientOptions
from google.cloud import documentai_v1 as documentai

from .documents import Page, TextBlock


def _document_ai_pages(document: documentai.Document) -> list[Page]:
    pages: list[Page] = []
    full_text = document.text or ""
    for number, page in enumerate(document.pages, 1):
        text = "".join(full_text[int(segment.start_index):int(segment.end_index)]
                       for segment in page.layout.text_anchor.text_segments)
        try:
            source_blocks = page.blocks
        except AttributeError:
            source_blocks = ()
        try:
            source_tokens = page.tokens
        except AttributeError:
            source_tokens = ()
        blocks = tuple(TextBlock(
            "".join(full_text[int(segment.start_index):int(segment.end_index)]
                    for segment in block.layout.text_anchor.text_segments),
            float(block.layout.confidence)) for block in source_blocks)
        confidences = [float(token.layout.confidence) for token in source_tokens]
        pages.append(Page(number, text, blocks,
                          sum(confidences) / len(confidences) if confidences else None))
    return pages


def _docai_endpoint(processor_name: str) -> str:
    parts = processor_name.split("/")
    location = parts[parts.index("locations") + 1] if "locations" in parts else "us"
    return f"{location}-documentai.googleapis.com"


def process_document(processor: str, file_bytes: bytes, mime_type: str, client=None) -> list[Page]:
    client = client if client is not None else documentai.DocumentProcessorServiceClient(
        client_options=ClientOptions(api_endpoint=_docai_endpoint(processor)))
    request = documentai.ProcessRequest(
        name=processor, raw_document=documentai.RawDocument(content=file_bytes, mime_type=mime_type))
    return _document_ai_pages(client.process_document(request=request).document)
