from __future__ import annotations

import csv
import io
import os
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory

from pypdf import PdfReader

from .documents import LayoutToken, Page, Point, TextBlock, TextSpan


def rectangle(left: float, top: float, width: float, height: float,
              page_width: float, page_height: float) -> tuple[Point, ...]:
    if page_width <= 0 or page_height <= 0:
        return ()
    return (Point(left / page_width, top / page_height),
            Point((left + width) / page_width, top / page_height),
            Point((left + width) / page_width, (top + height) / page_height),
            Point(left / page_width, (top + height) / page_height))


def _tsv_page(number: int, tsv: str) -> Page:
    rows = list(csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE))
    dimensions = next((row for row in rows if row["level"] == "1"), {})
    width, height = float(dimensions.get("width", 0)), float(dimensions.get("height", 0))
    blocks: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        if row["level"] != "5" or not row["text"].strip():
            continue
        confidence = float(row["conf"])
        if not 0 <= confidence <= 100:
            raise ValueError("Local OCR returned a word without confidence.")
        blocks.setdefault(row["block_num"], []).append(row)
    result = []
    text = ""
    scores = []
    for words in blocks.values():
        if text:
            text += "\n\n"
        start = len(text)
        tokens = []
        for word in words:
            if tokens:
                text += " "
            word_start = len(text)
            text += word["text"]
            tokens.append(LayoutToken(word["text"], rectangle(
                float(word["left"]), float(word["top"]),
                float(word["width"]), float(word["height"]), width, height),
                (TextSpan(word_start, len(text)),)))
            scores.append(float(word["conf"]) / 100)
        points = [p for token in tokens for p in token.polygon]
        polygon = ()
        if points:
            left, top = min(p.x for p in points), min(p.y for p in points)
            right, bottom = max(p.x for p in points), max(p.y for p in points)
            polygon = rectangle(left, top, right-left, bottom-top, 1, 1)
        result.append(TextBlock(text[start:], min(float(w["conf"])/100 for w in words),
                                polygon, (TextSpan(start, len(text)),), tuple(tokens)))
    return Page(number, text, tuple(result), sum(scores)/len(scores) if scores else None,
                text, width > 0 and height > 0)


def pdf_text_pages(file_path: str) -> list[Page]:
    result = subprocess.run(
        ["pdftotext", "-bbox-layout", file_path, "-"],
        check=True, capture_output=True, timeout=120)
    root = ET.fromstring(result.stdout)
    ns = "{http://www.w3.org/1999/xhtml}"
    pages = []
    for number, page in enumerate(root.iter(ns + "page"), 1):
        width, height = float(page.attrib["width"]), float(page.attrib["height"])
        text = ""
        blocks = []
        for block in page.iter(ns + "block"):
            if text:
                text += "\n\n"
            start = len(text)
            tokens = []
            for word in block.iter(ns + "word"):
                if tokens:
                    text += " "
                word_start = len(text)
                value = "".join(word.itertext())
                text += value
                x0, y0 = float(word.attrib["xMin"]), float(word.attrib["yMin"])
                x1, y1 = float(word.attrib["xMax"]), float(word.attrib["yMax"])
                tokens.append(LayoutToken(value, rectangle(x0, y0, x1-x0, y1-y0, width, height),
                                          (TextSpan(word_start, len(text)),)))
            if tokens:
                x0, y0 = float(block.attrib["xMin"]), float(block.attrib["yMin"])
                x1, y1 = float(block.attrib["xMax"]), float(block.attrib["yMax"])
                blocks.append(TextBlock(text[start:], 1.0,
                                        rectangle(x0, y0, x1-x0, y1-y0, width, height),
                                        (TextSpan(start, len(text)),), tuple(tokens)))
        pages.append(Page(number, text, tuple(blocks), layout_text=text, layout_available=True))
    return pages


def native_pages_complete(file_path: str, pages: list[Page], expected_count: int) -> bool:
    if len(pages) != expected_count or not any(page.text.strip() for page in pages):
        return False
    empty = [page for page in pages if not page.text.strip()]
    if not empty:
        return True
    reader = PdfReader(file_path)
    paint = {b"Tj", b"TJ", b"'", b'"', b"Do", b"INLINE IMAGE",
             b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"sh"}
    for page in empty:
        content = reader.pages[page.number - 1].get_contents()
        if content is not None and any(operator in paint for _, operator in content.operations):
            return False
    return True


class LocalDocumentAdapter:
    def page_texts(self, file_bytes: bytes, mime_type: str) -> list[Page]:
        suffixes = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}
        if mime_type not in suffixes:
            raise ValueError(f"Unsupported local OCR type: {mime_type}")
        timeout = int(os.getenv("RECOUP_LOCAL_OCR_TIMEOUT_SECONDS", "120"))
        deadline = time.monotonic() + timeout

        def remaining():
            seconds = deadline - time.monotonic()
            if seconds <= 0:
                raise TimeoutError("Local OCR timed out.")
            return seconds

        with TemporaryDirectory(prefix="recoup-ocr-") as directory:
            root = Path(directory)
            source = root / ("document" + suffixes[mime_type])
            source.write_bytes(file_bytes)
            images = [source]
            if mime_type == "application/pdf":
                if not 0 < len(PdfReader(io.BytesIO(file_bytes)).pages) <= 25:
                    raise ValueError("Local scanned PDF OCR requires 1–25 pages.")
                subprocess.run(["pdftoppm", "-r", "150", "-scale-to", "2400",
                                "-png", str(source), str(root / "page")],
                               check=True, capture_output=True, timeout=remaining())
                images = sorted(root.glob("page-*.png"), key=lambda path: int(path.stem.split("-")[-1]))
            pages = []
            for number, image in enumerate(images, 1):
                result = subprocess.run(
                    ["tesseract", str(image), "stdout", "-l",
                     os.getenv("RECOUP_LOCAL_OCR_LANGUAGES", "eng"), "tsv"],
                    check=True, capture_output=True, text=True, timeout=remaining())
                pages.append(_tsv_page(number, result.stdout))
            if not any(page.text.strip() for page in pages):
                raise ValueError("Local OCR returned no text.")
            return pages
