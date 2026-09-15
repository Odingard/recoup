from __future__ import annotations

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..cloud.models import GenerationConfig, get_model_adapter
from .extractor import PageAnchoredEntitlement, _client as extraction_client, _transient
from .pages import Page

OCR_CONFIDENCE_GATE = float(os.getenv("RECOUP_OCR_CONFIDENCE_GATE", "0.85"))

VERIFY_PROMPT = """For each item, you are given the text of one contract page and a claimed financial term extracted from it. Decide whether the page text SUPPORTS the claim exactly as stated (same number, same unit, same dates), CONTRADICTS it (the page states a different value, or the quoted clause does not mean what the claim says), or is UNCLEAR. Do not use outside knowledge. Answer for every index."""


class VerificationItem(BaseModel):
    index: int
    verdict: Literal["supports", "contradicts", "unclear"]
    reason: str = ""

    @field_validator("verdict", mode="before")
    @classmethod
    def _map_verdict(cls, value) -> str:
        text = str(value or "").strip().lower()
        if "contradict" in text:
            return "contradicts"
        if "support" in text:
            return "supports"
        return "unclear"


class VerificationBatch(BaseModel):
    results: list[VerificationItem] = Field(default_factory=list)


class VerifiedEntitlement(PageAnchoredEntitlement):
    verification: dict


def _client():
    return extraction_client()


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _ocr_confidence(page: Page | None, quote: str) -> float | None:
    """Min confidence of the cited page's blocks overlapping the quote; page
    mean when no block overlaps; None when the OCR path provides no signal."""
    if page is None:
        return None
    overlapping = []
    normed_quote = _norm(quote)
    if page.blocks and normed_quote:
        for block in page.blocks:
            block_text = _norm(block.text)
            if len(block_text) < 8:
                continue
            if normed_quote[:40] in block_text or block_text[:40] in normed_quote:
                overlapping.append(block.confidence)
    if overlapping:
        return min(overlapping)
    return page.ocr_confidence


def _model_check(client, model: str, items: list[dict]) -> list[VerificationItem]:
    contents = VERIFY_PROMPT + "\n" + json.dumps(items, default=str)
    last = None
    for attempt in range(3):
        try:
            response = get_model_adapter(client).generate(
                model=model, contents=contents,
                config=GenerationConfig(
                    response_mime_type="application/json", response_schema=VerificationBatch,
                    temperature=0.0),
            )
            parsed = getattr(response, "parsed", None)
            if parsed is not None:
                return (parsed if isinstance(parsed, VerificationBatch)
                        else VerificationBatch.model_validate(parsed)).results
            raw = json.loads(getattr(response, "text", response))
            if isinstance(raw, list):
                return [VerificationItem.model_validate(item) for item in raw]
            return VerificationBatch.model_validate(raw).results
        except Exception as exc:
            last = exc
            if attempt == 2 or not _transient(exc):
                raise
            time.sleep((2, 6)[attempt])
    raise last


def verify(result, pages: list[Page], *, client=None, model: str | None = None) -> list[VerifiedEntitlement]:
    client = client or _client()
    model = model or os.getenv("RECOUP_VERIFY_MODEL", "gemini-2.5-flash")
    page_map = {page.number: page.text for page in pages}
    page_objs: dict[int, Page] = {page.number: page for page in pages}
    prepared = []
    for entitlement in result.entitlements:
        quote = _norm(entitlement.provenance)[:120]
        cited = _norm(page_map.get(entitlement.page, ""))
        quote_found = bool(quote) and quote in cited
        page_matched = quote_found
        if not quote_found and quote:
            for number, text in page_map.items():
                if quote in _norm(text):
                    entitlement.page = number
                    quote_found = True
                    page_matched = False
                    break
        prepared.append((entitlement, quote_found, page_matched))

    checks: dict[int, VerificationItem] = {}
    eligible = [(index, ent, found) for index, (ent, found, _matched) in enumerate(prepared)
                if found and ent.confidence_score >= 0.6]
    for start in range(0, len(eligible), 20):
        batch = eligible[start:start + 20]
        payload = [{"index": index, "page": ent.page, "page_text": page_map.get(ent.page, ""),
                    "term": ent.model_dump()} for index, ent, _ in batch]
        try:
            items = _model_check(client, model, payload)
        except Exception as exc:
            logger.warning("model verification batch failed; marking items unclear: %s", exc)
            items = [VerificationItem(index=index, verdict="unclear",
                                      reason=f"model verification unavailable: {exc}")
                     for index, _ent, _found in batch]
        logger.info("verifier verdicts: %s",
                    [(i.index, i.verdict) for i in items])
        for item in items:
            checks[item.index] = item

    verified: list[VerifiedEntitlement] = []
    for index, (entitlement, quote_found, page_matched) in enumerate(prepared):
        item = checks.get(index)
        verdict = (item.verdict if item else "skipped").strip().lower()
        if verdict not in {"supports", "contradicts", "unclear", "skipped"}:
            verdict = "unclear"
        factors = {"supports": 1.0, "unclear": 0.7, "contradicts": 0.2, "skipped": 1.0}
        ocr_conf = _ocr_confidence(page_objs.get(entitlement.page),
                                   _norm(entitlement.provenance)[:120])
        final = min(float(entitlement.confidence_score),
                    0.5 if not quote_found else 1.0,
                    0.9 if not page_matched else 1.0,
                    factors.get(verdict, 0.7 if item else 1.0),
                    *(0.7,) if ocr_conf is not None and ocr_conf < OCR_CONFIDENCE_GATE else ())
        verification = {"quote_found": quote_found, "page_matched": page_matched,
                        "model_check": verdict, "final_confidence": round(final, 4),
                        "ocr_confidence": round(ocr_conf, 4) if ocr_conf is not None else None,
                        "ocr_gate": bool(ocr_conf is not None and ocr_conf < OCR_CONFIDENCE_GATE)}
        data = entitlement.model_dump()
        data["confidence_score"] = round(final, 4)
        data["verification"] = verification
        verified.append(VerifiedEntitlement.model_validate(data))
    return verified
