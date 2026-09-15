from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

from pydantic import BaseModel, Field

from ..cloud.models import GenerationConfig, get_model_adapter
from .ocr import get_ocr_adapter
from .chunker import Chunk, chunk_pages
from .ontology import FINANCIAL_RIGHT_TYPES, families
from .pages import Page, load_pages

SYSTEM = """You are extracting contractual financial entitlements for a revenue-recovery audit. You only report terms that are stated in the document text provided. Every entitlement must carry: the exact verbatim quote as provenance, the page number from the [[PAGE n]] marker where that quote appears, and the section reference if the document has one. Never infer, estimate, or normalise a number that is not written in the text. If an amendment, addendum, order form, or exhibit changes a term, emit both the original and the changed value as separate entitlements, each with its own effective_date. Where the document uses a defined term (for example "Committed Volume" or "Fees"), resolve it using the document's own definitions section and cite both pages in provenance. Percentages are decimals (0.05 for 5%). Dates are ISO YYYY-MM-DD. If you are not certain a term is stated, set confidence_score below 0.6 rather than omitting or guessing.\n"""

RULES = """Rules: (1) If an amendment or addendum changes a term (e.g. lowers the committed minimum), emit BOTH the original and the amended value as separate entitlements, each with its own effective_date. (2) For discounts and promotions, put the promo name in label, when it begins in start_date and when it ends in end_date; never in effective_date. (3) Emit included_units whenever the base fee 'includes' a quantity of units. (4) Only report overage_rate for a per-unit charge that applies ABOVE an included quantity; a per-unit list price that is simply billed per unit is not an overage rate. (5) provenance must be the verbatim clause text. (6) If overage pricing is tiered (different per-unit rates for different volume bands above the included quantity), emit one overage_tier entitlement per band with value = that band's per-unit rate and tier_up_to = the band's upper bound in units above the included quantity (null for the last band), instead of a single overage_rate. (7) Emit term_start and term_end for the initial term's start and end dates (value=0, date in effective_date). (8) Emit auto_renewal when the contract renews automatically (value = renewal term length in months, 0 if unstated) and renewal_notice_days for the notice period required to cancel before renewal. (9) For per-seat pricing, emit committed_seats (the seat/user/license count) and seat_price (the monthly price per seat)."""

ALL_FAMILIES = "Extract every committed minimum, included units, overage rate or tier, discount or promotion, escalator, initial term start/end, auto-renewal, renewal notice period, committed seats and seat price stated in the following pages. Each page is preceded by a [[PAGE n]] marker; report that n as page.\n" + RULES


class PageAnchoredEntitlement(BaseModel):
    term_type: str
    value: float
    label: Optional[str] = None
    effective_date: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    tier_up_to: Optional[float] = None
    confidence_score: float
    provenance: str
    page: int = 1
    section_ref: Optional[str] = None


ROLE_VALUES = {"master", "amendment", "order_form", "exhibit", "sow", "other"}

CLASSIFY_PROMPT = """Classify this contract document. role is one of: master (a master agreement, MSA, subscription or services agreement that stands on its own), amendment (an amendment, addendum, or change order that modifies an earlier agreement), order_form (an order form, sales order, or quote that supplies quantities and prices under a master agreement), exhibit (an exhibit, schedule, or attachment to a master agreement), sow (statement of work), other. title is the document's own title as written. counterparty is the customer/licensee/client party name as written. effective_date is the document's stated effective date (ISO YYYY-MM-DD) or null. references lists the titles or dates of other agreements this document says it modifies, attaches to, or is governed by, verbatim. amendment_number is the number if the title says "Amendment No. N" or similar, else null. Use only what is written."""


class DocumentProfile(BaseModel):
    role: str = "other"
    title: Optional[str] = None
    counterparty: Optional[str] = None
    effective_date: Optional[str] = None
    references: list[str] = Field(default_factory=list)
    amendment_number: Optional[int] = None


class ChunkExtraction(BaseModel):
    customer_name: Optional[str] = None
    entitlements: list[PageAnchoredEntitlement] = Field(default_factory=list)
    document: Optional[DocumentProfile] = None


@dataclass
class ExtractionResult:
    entitlements: list[PageAnchoredEntitlement]
    customer_name: str
    pages: int
    source_kind: str
    model: str
    chunks: int
    cached: bool
    document: DocumentProfile


def _client():
    return get_model_adapter()


def _transient(exc: Exception) -> bool:
    text = str(exc).upper()
    status = str(getattr(exc, "status_code", ""))
    return any(token in text or token in status for token in ("429", "500", "502", "503", "504", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE"))


_ROLE_RULES: tuple[tuple[str, str], ...] = (
    (r"amendment|addendum", "amendment"),
    (r"order[ _-]?form|sales[ _-]?order|quote", "order_form"),
    (r"exhibit|schedule|attachment", "exhibit"),
    (r"statement[ _-]of[ _-]work|\bsow\b", "sow"),
)


def _override_role(role: str, file_name: str | None, first_page_text: str) -> str:
    """Deterministic filename/title classification wins over the model."""
    haystacks = [file_name or "", first_page_text[:300]]
    for pattern, value in _ROLE_RULES:
        for hay in haystacks:
            if re.search(pattern, hay, re.IGNORECASE):
                return value
    return role if role in ROLE_VALUES else "other"


def _amendment_number(profile: DocumentProfile, file_name: str | None, first_page_text: str) -> Optional[int]:
    if profile.amendment_number is not None:
        return profile.amendment_number
    for hay in (profile.title or "", file_name or "", first_page_text[:300]):
        match = re.search(r"amendment\s*(?:no\.?|number|#)?\s*(\d+)", hay, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def _classify(client, model: str, *, text: str, cached_content=None,
              file_name: str | None, first_page_text: str) -> DocumentProfile:
    try:
        kwargs = {"response_mime_type": "application/json", "response_schema": DocumentProfile,
                  "temperature": 0.0}
        if cached_content:
            kwargs["cached_content"] = cached_content
        response = _call(client, model,
                         CLASSIFY_PROMPT if cached_content else f"{CLASSIFY_PROMPT}\n\n{text[:60000]}",
                         GenerationConfig(**kwargs))
        parsed = getattr(response, "parsed", None)
        if parsed is None:
            raw = getattr(response, "text", response)
            parsed = DocumentProfile.model_validate_json(raw) if isinstance(raw, str) else DocumentProfile.model_validate(raw)
        profile = parsed if isinstance(parsed, DocumentProfile) else DocumentProfile.model_validate(parsed)
    except Exception:
        logger.warning("document classification failed; defaulting to 'other'")
        profile = DocumentProfile()
    profile.role = _override_role(profile.role, file_name, first_page_text)
    if profile.role == "amendment":
        profile.amendment_number = _amendment_number(profile, file_name, first_page_text)
    return profile


def _call(client, model: str, contents, config):
    last = None
    for attempt in range(3):
        try:
            return get_model_adapter(client).generate(model=model, contents=contents, config=config)
        except Exception as exc:
            last = exc
            if attempt == 2 or not _transient(exc):
                raise
            time.sleep((2, 6)[attempt])
    raise last


def _config(*, cached_content=None):
    kwargs = {"response_mime_type": "application/json", "response_schema": ChunkExtraction, "temperature": 0.0}
    if cached_content:
        kwargs["cached_content"] = cached_content
    return GenerationConfig(**kwargs)


def _parse_response(response) -> ChunkExtraction:
    parsed = getattr(response, "parsed", None)
    if parsed is not None:
        return parsed if isinstance(parsed, ChunkExtraction) else ChunkExtraction.model_validate(parsed)
    raw = getattr(response, "text", response)
    if isinstance(raw, str):
        return ChunkExtraction.model_validate_json(raw)
    return ChunkExtraction.model_validate(raw)


def _dedupe(items: list[PageAnchoredEntitlement]) -> list[PageAnchoredEntitlement]:
    seen = set()
    result = []
    for item in items:
        key = (item.term_type, item.value, item.effective_date, item.start_date, item.end_date, item.page)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _family_prompt(term_types, signals) -> str:
    return (f"Focus only on: {', '.join(term_types)}. "
            f"Typical wording: {', '.join(signals)}. "
            f"Ignore all other kinds of terms.\n{ALL_FAMILIES}")


def extract_pages(pages: list[Page], source_kind: str, *, client=None, model: str | None = None,
                  file_name: str | None = None) -> ExtractionResult:
    client = get_model_adapter(client) if client is not None else _client()
    model = model or os.getenv("RECOUP_EXTRACTION_MODEL", "gemini-2.5-pro")
    chunks = chunk_pages(pages)
    full_text = "\n\n".join(chunk.text for chunk in chunks)
    extracted: list[PageAnchoredEntitlement] = []
    customer_name = "Unknown"
    cached = False
    profile: DocumentProfile = DocumentProfile()
    first_page_text = pages[0].text if pages else ""
    if len(full_text) > 200_000:
        cache = None
        try:
            cache = client.create_cache(model=model, text=full_text, system=SYSTEM, ttl="1800s")
            cached = True
            profile = _classify(client, model, text=chunks[0].text if chunks else "",
                                cached_content=cache, file_name=file_name,
                                first_page_text=first_page_text)
            for family, term_types in families().items():
                signals = sorted({phrase for term in term_types for phrase in FINANCIAL_RIGHT_TYPES[term].signal_phrases})
                response = _call(client, model, _family_prompt(term_types, signals), _config(cached_content=cache))
                parsed = _parse_response(response)
                extracted.extend(parsed.entitlements)
                if parsed.customer_name and customer_name == "Unknown":
                    customer_name = parsed.customer_name
        except Exception:
            cached = False
            extracted = []
        finally:
            if cache is not None:
                try:
                    client.delete_cache(cache)
                except Exception:
                    pass
    if not cached:
        if chunks:
            profile = _classify(client, model, text=chunks[0].text,
                                file_name=file_name, first_page_text=first_page_text)
        for chunk in chunks:
            response = _call(client, model, f"{ALL_FAMILIES}\n{chunk.text}", _config())
            parsed = _parse_response(response)
            extracted.extend(parsed.entitlements)
            if parsed.customer_name and customer_name == "Unknown":
                customer_name = parsed.customer_name
        if os.getenv("RECOUP_EXTRACTION_RECALL_PASS", "1") != "0":
            lower = full_text.lower()
            for family, term_types in families().items():
                signals = sorted({phrase for term in term_types for phrase in FINANCIAL_RIGHT_TYPES[term].signal_phrases})
                if not any(s.lower() in lower for s in signals):
                    continue
                missing = [t for t in term_types
                           if not any(e.term_type == t for e in extracted)]
                if not missing:
                    continue
                try:
                    for chunk in chunks:
                        response = _call(client, model,
                                         f"{_family_prompt(term_types, signals)}\n{chunk.text}",
                                         _config())
                        parsed = _parse_response(response)
                        extracted.extend(parsed.entitlements)
                        if parsed.customer_name and customer_name == "Unknown":
                            customer_name = parsed.customer_name
                except Exception:
                    logger.warning("recall pass for %s failed; continuing", term_types)
    return ExtractionResult(_dedupe(extracted), customer_name, len(pages), source_kind, model,
                            len(chunks), cached, profile)


def extract(file_path: str, *, pages: list[Page] | None = None, source_kind: str | None = None, client=None, model: str | None = None) -> ExtractionResult:
    if pages is None or source_kind is None:
        pages, source_kind = load_pages(file_path)
    if source_kind in {"scanned", "image"}:
        mime = "application/pdf" if file_path.lower().endswith(".pdf") else "image/jpeg"
        with open(file_path, "rb") as fh:
            pages = get_ocr_adapter(client=client).page_texts(fh.read(), mime)
    return extract_pages(pages, source_kind, client=client, model=model,
                         file_name=os.path.basename(file_path))
