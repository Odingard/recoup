from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from ..ingestion_doc import _docx_to_text

MAX_DOCUMENT_PAGES = 600
MAX_SCANNED_PDF_PAGES = 25


@dataclass(frozen=True)
class Page:
    number: int
    text: str


class DocumentTooLargeError(Exception):
    def __init__(self, pages: int):
        self.pages = pages
        super().__init__(f"Agreement exceeds {MAX_DOCUMENT_PAGES} pages; split it by section and re-upload")


def _pseudo_pages(text: str) -> list[Page]:
    size = 3000
    return [Page(i + 1, text[start:start + size]) for i, start in enumerate(range(0, len(text), size))] or [Page(1, "")]


def load_pages(file_path: str) -> tuple[list[Page], str]:
    suffix = Path(file_path).suffix.lower()
    if suffix == ".pdf":
        reader = PdfReader(file_path)
        count = len(reader.pages)
        if count > MAX_DOCUMENT_PAGES:
            raise DocumentTooLargeError(count)
        texts: list[str] = []
        for page in reader.pages:
            try:
                texts.append(page.extract_text() or "")
            except Exception:
                texts.append("")
        average = (sum(len(text) for text in texts) / count) if count else 0
        if count and average < 200:
            return [Page(i + 1, "") for i in range(count)], "scanned"
        return [Page(i + 1, text) for i, text in enumerate(texts)], "pdf_text"
    if suffix == ".docx":
        return _pseudo_pages(_docx_to_text(file_path).decode("utf-8", errors="replace")), "docx"
    if suffix in {".txt", ".md"}:
        return _pseudo_pages(Path(file_path).read_text(errors="replace")), "text"
    if suffix in {".png", ".jpg", ".jpeg"}:
        return [Page(1, "")], "image"
    raise ValueError(f"Unsupported document type: {suffix}")
