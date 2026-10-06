"""NVIDIA Nemotron OCR: the NVIDIA-hosted API or a self-hosted NIM `/v1/ocr`."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from statistics import median
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, build_opener

from .documents import LayoutToken, Page, Point, TextBlock, TextSpan
from .local_documents import ocr_deadline, page_images, rectangle
from .models import ProviderConfigurationError
from .remote_models import NoRedirects

Word = tuple[str, float, tuple[Point, ...]]
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg"}


class OcrRequestError(RuntimeError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"NVIDIA OCR request failed ({status_code}).")


def _box(word: Word) -> tuple[float, float, float, float]:
    xs, ys = [p.x for p in word[2]], [p.y for p in word[2]]
    return min(xs), min(ys), max(xs), max(ys)


def _middle(word: Word) -> float:
    _, top, _, bottom = _box(word)
    return (top + bottom) / 2


def _blocks(words: list[Word]) -> list[list[list[Word]]]:
    """Group word detections into lines by vertical centre, then lines into
    blocks wherever the gap between lines is at most one median word height."""
    height = median(_box(w)[3] - _box(w)[1] for w in words)
    lines: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (_middle(w), _box(w)[0])):
        if lines and abs(_middle(word) - sum(map(_middle, lines[-1])) / len(lines[-1])) <= height / 2:
            lines[-1].append(word)
        else:
            lines.append([word])
    lines = [sorted(line, key=lambda w: _box(w)[0]) for line in lines]
    blocks = [[lines[0]]]
    for previous, line in zip(lines, lines[1:]):
        gap = min(_box(w)[1] for w in line) - max(_box(w)[3] for w in previous)
        if gap <= height:
            blocks[-1].append(line)
        else:
            blocks.append([line])
    return blocks


def _nim_page(number: int, detections: list[dict]) -> Page:
    words: list[Word] = []
    for detection in detections:
        prediction = detection["text_prediction"]
        text = (prediction.get("text") or "").strip()
        if not text:
            continue
        confidence = prediction.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("NVIDIA OCR returned a word without confidence.")
        polygon = tuple(Point(float(p["x"]), float(p["y"])) for p in detection["bounding_box"]["points"])
        words.append((text, float(confidence), polygon))
    if not words:
        return Page(number, "", (), None, "", True)
    text = ""
    blocks = []
    for lines in _blocks(words):
        if text:
            text += "\n\n"
        start = len(text)
        tokens = []
        for index, line in enumerate(lines):
            if index:
                text += "\n"
            for position, (value, _, polygon) in enumerate(line):
                if position:
                    text += " "
                word_start = len(text)
                text += value
                tokens.append(LayoutToken(value, polygon, (TextSpan(word_start, len(text)),)))
        members = [word for line in lines for word in line]
        left = min(_box(w)[0] for w in members)
        top = min(_box(w)[1] for w in members)
        right = max(_box(w)[2] for w in members)
        bottom = max(_box(w)[3] for w in members)
        blocks.append(TextBlock(text[start:], min(w[1] for w in members),
                                rectangle(left, top, right - left, bottom - top, 1, 1),
                                (TextSpan(start, len(text)),), tuple(tokens)))
    scores = [word[1] for word in words]
    return Page(number, text, tuple(blocks), sum(scores) / len(scores), text, True)


class NvidiaOcrAdapter:
    def __init__(self):
        self.url = os.getenv("RECOUP_NVIDIA_OCR_URL", "").strip()
        url = urlparse(self.url)
        if url.scheme not in {"https", "http"} or not url.hostname:
            raise ProviderConfigurationError("Set RECOUP_NVIDIA_OCR_URL for NVIDIA OCR.")
        if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ProviderConfigurationError("NVIDIA OCR endpoints require HTTPS.")
        if url.username or url.password or url.query or url.fragment:
            raise ProviderConfigurationError("NVIDIA OCR URL must not contain credentials, query or fragment.")
        self.opener = build_opener(NoRedirects())

    def _detections(self, image: Path, remaining) -> list[dict]:
        encoded = base64.b64encode(image.read_bytes()).decode()
        payload = {"input": [{"type": "image_url", "url": f"data:{IMAGE_TYPES[image.suffix]};base64,{encoded}"}],
                   "merge_levels": ["word"]}
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        key = os.getenv("RECOUP_NVIDIA_API_KEY", "").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        request = Request(self.url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with self.opener.open(request, timeout=remaining()) as response:
                body = json.load(response)
        except HTTPError as exc:
            raise OcrRequestError(exc.code) from None
        data = body.get("data")
        if not isinstance(data, list) or len(data) != 1 or data[0].get("index", 0) != 0:
            raise ValueError("NVIDIA OCR response was incomplete.")
        return data[0]["text_detections"]

    def page_texts(self, file_bytes: bytes, mime_type: str) -> list[Page]:
        remaining = ocr_deadline(int(os.getenv("RECOUP_NVIDIA_OCR_TIMEOUT_SECONDS", "120")))
        with page_images(file_bytes, mime_type, remaining) as images:
            pages = [_nim_page(number, self._detections(image, remaining))
                     for number, image in enumerate(images, 1)]
        if not any(page.text.strip() for page in pages):
            raise ValueError("NVIDIA OCR returned no text.")
        return pages
