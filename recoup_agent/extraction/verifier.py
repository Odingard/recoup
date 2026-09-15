from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field

from .extractor import PageAnchoredEntitlement, _client as extraction_client, _transient
from .pages import Page

VERIFY_PROMPT = """For each item, you are given the text of one contract page and a claimed financial term extracted from it. Decide whether the page text SUPPORTS the claim exactly as stated (same number, same unit, same dates), CONTRADICTS it (the page states a different value, or the quoted clause does not mean what the claim says), or is UNCLEAR. Do not use outside knowledge. Answer for every index."""


class VerificationItem(BaseModel):
    index: int
    verdict: str
    reason: str = ""


class VerificationBatch(BaseModel):
    results: list[VerificationItem] = Field(default_factory=list)


class VerifiedEntitlement(PageAnchoredEntitlement):
    verification: dict


def _client():
    return extraction_client()


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _model_check(client, model: str, items: list[dict]) -> list[VerificationItem]:
    from google.genai import types
    contents = VERIFY_PROMPT + "\n" + json.dumps(items, default=str)
    last = None
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=model, contents=contents,
                config=types.GenerateContentConfig(
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
            import time
            time.sleep((2, 6)[attempt])
    raise last


def verify(result, pages: list[Page], *, client=None, model: str | None = None) -> list[VerifiedEntitlement]:
    client = client or _client()
    model = model or os.getenv("RECOUP_VERIFY_MODEL", "gemini-2.5-flash")
    page_map = {page.number: page.text for page in pages}
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
        for item in items:
            checks[item.index] = item

    verified: list[VerifiedEntitlement] = []
    for index, (entitlement, quote_found, page_matched) in enumerate(prepared):
        item = checks.get(index)
        verdict = item.verdict if item else "skipped"
        factors = {"supports": 1.0, "unclear": 0.7, "contradicts": 0.2, "skipped": 1.0}
        final = min(float(entitlement.confidence_score),
                    0.5 if not quote_found else 1.0,
                    0.9 if not page_matched else 1.0,
                    factors.get(verdict, 0.7 if item else 1.0))
        verification = {"quote_found": quote_found, "page_matched": page_matched,
                        "model_check": verdict, "final_confidence": round(final, 4)}
        data = entitlement.model_dump()
        data["confidence_score"] = round(final, 4)
        data["verification"] = verification
        verified.append(VerifiedEntitlement.model_validate(data))
    return verified
