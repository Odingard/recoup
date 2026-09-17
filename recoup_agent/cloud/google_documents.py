from __future__ import annotations

from google.api_core.client_options import ClientOptions
from google.cloud import documentai_v1 as documentai

from .documents import LayoutToken, Page, Point, TextBlock, TextSpan


def _spans(layout: documentai.Document.Page.Layout) -> tuple[TextSpan, ...]:
    try:
        return tuple(TextSpan(int(s.start_index), int(s.end_index))
                     for s in layout.text_anchor.text_segments)
    except AttributeError:
        return ()


def _polygon(layout: documentai.Document.Page.Layout, width: float, height: float) -> tuple[Point, ...]:
    try:
        polygon = layout.bounding_poly
    except AttributeError:
        return ()
    if polygon.normalized_vertices:
        return tuple(Point(float(p.x), float(p.y)) for p in polygon.normalized_vertices)
    if width > 0 and height > 0:
        return tuple(Point(float(p.x) / width, float(p.y) / height) for p in polygon.vertices)
    return ()


def _text(source: str, spans: tuple[TextSpan, ...]) -> str:
    return "".join(source[s.start:s.end] for s in spans)


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
        try:
            width, height = float(page.dimension.width), float(page.dimension.height)
        except AttributeError:
            width, height = 0, 0
        tokens = tuple(LayoutToken(
            _text(full_text, _spans(token.layout)),
            _polygon(token.layout, width, height), _spans(token.layout))
            for token in source_tokens)
        blocks = []
        for block in source_blocks:
            spans = _spans(block.layout)
            polygon = _polygon(block.layout, width, height)
            members = tuple(token for token in tokens if any(
                s.start < b.end and s.end > b.start for s in token.spans for b in spans))
            blocks.append(TextBlock(_text(full_text, spans), float(block.layout.confidence),
                                    polygon, spans, members))
        confidences = [float(token.layout.confidence) for token in source_tokens]
        pages.append(Page(number, text, tuple(blocks),
                          sum(confidences) / len(confidences) if confidences else None,
                          full_text, True))
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
