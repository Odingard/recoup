from __future__ import annotations

import csv
import io
import os
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from pypdf import PdfReader

from .documents import Page, TextBlock


def _tsv_page(number: int, tsv: str) -> Page:
    blocks: dict[str, list[tuple[str, float]]] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row["level"] != "5" or not row["text"].strip():
            continue
        confidence = float(row["conf"])
        if not 0 <= confidence <= 100:
            raise ValueError("Local OCR returned a word without confidence.")
        blocks.setdefault(row["block_num"], []).append((row["text"], confidence / 100))
    result = tuple(TextBlock(" ".join(word for word, _ in words),
                             min(score for _, score in words))
                   for words in blocks.values())
    scores = [score for words in blocks.values() for _, score in words]
    return Page(number, "\n\n".join(block.text for block in result), result,
                sum(scores) / len(scores) if scores else None)


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
