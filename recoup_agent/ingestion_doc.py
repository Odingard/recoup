import logging
import mimetypes
import os
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from typing import List, Literal, Optional

from pydantic import BaseModel, Field
from .cloud import models as genai
from .cloud.models import BinaryPart, GenerationConfig, get_model_adapter
from .cloud.local_documents import native_pages_complete, pdf_text_pages
from .cloud.visual_structure import inspect_raster
from .document_quality import LowConfidenceGateException, StructuralIssue, inspect_structure

logger = logging.getLogger(__name__)

UNREADABLE_DOCUMENT_MESSAGE = (
    "We couldn't read this file. Make sure it's a valid PDF, DOCX, or "
    "scanned image and try again."
)


class UnreadableDocumentError(Exception):
    """The document or document provider could not read the uploaded file."""

    def __init__(self):
        super().__init__(UNREADABLE_DOCUMENT_MESSAGE)

_MIME_OVERRIDES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
}


def _docx_to_text(file_path: str) -> bytes:
    """Extract paragraph text from a .docx using only stdlib (zipfile + XML)."""
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(file_path) as zf:
        xml_bytes = zf.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    paragraphs = []
    for para in root.iter(f"{ns}p"):
        text = "".join(node.text or "" for node in para.iter(f"{ns}t"))
        if text.strip():
            paragraphs.append(text)
    return "\n".join(paragraphs).encode("utf-8")

class Entitlement(BaseModel):
    term_type: str = Field(description="The type of entitlement, e.g., 'committed_minimum', 'overage_rate', 'discount', 'escalator', 'term_start', 'term_end', 'auto_renewal', 'renewal_notice_days', 'committed_seats', 'seat_price'")
    value: float = Field(description="The numeric value of the entitlement. For percentages, use decimals (e.g. 0.05 for 5%).")
    amount_period: Optional[Literal["month", "quarter", "year", "unknown"]] = Field(
        None, description="For committed_minimum, the period covered by the literal amount: month, quarter, year, or unknown if unstated. Do not convert the amount. Leave null for other terms.")
    label: Optional[str] = Field(None, description="Short human label for this term as it might appear on an invoice line, e.g. 'Launch promo', 'Volume discount', 'Amendment 1'.")
    scope: Optional[str] = Field(
        None,
        description=(
            "Stable lower_snake_case name for the product or charge layer this "
            "term governs, e.g. platform or enterprise_workspace. Reuse the "
            "same scope when an amendment changes that term."
        ),
    )
    effective_date: Optional[str] = Field(None, description="For committed_minimum/overage_rate/escalator terms: the date this value takes effect (ISO YYYY-MM-DD). For an amendment that changes a term, emit a SEPARATE entitlement with the amendment's effective date. Do NOT use this field for discount start or end dates.")
    start_date: Optional[str] = Field(None, description="For discounts/promotions: the date the discount STARTS (ISO YYYY-MM-DD). Leave null otherwise.")
    end_date: Optional[str] = Field(None, description="Inclusive expiration date (ISO YYYY-MM-DD) for this financial term or discount. Leave null if no expiration is stated.")
    tier_up_to: Optional[float] = Field(None, description="For overage_tier terms only: the upper bound of this tier in overage units above the included quantity; null for the final, unbounded tier.")
    confidence_score: float = Field(description="Confidence score of this extraction between 0.0 and 1.0")
    provenance: str = Field(description="The exact clause quote and page number indicating where this was found.")
    page: Optional[int] = None
    section_ref: Optional[str] = None
    verification: Optional[dict] = None
    source_file: Optional[str] = None
    overrides_generic: bool = False

class ContractEntitlements(BaseModel):
    customer_name: str = Field(description="The name of the customer the contract is with.")
    entitlements: List[Entitlement]
    document: Optional[dict] = None
    structural_verification: Optional[dict] = None

def _extract_single_shot(file_path: str) -> ContractEntitlements:
    """Legacy single-shot extractor retained for controlled rollback."""
    try:
        suffix = os.path.splitext(file_path)[1].lower()
        mime_type, _ = mimetypes.guess_type(file_path)
        mime_type = _MIME_OVERRIDES.get(suffix, mime_type) or "application/octet-stream"

        with open(file_path, "rb") as f:
            file_bytes = f.read()

        if suffix == ".docx":
            # Gemini cannot read OOXML; convert to plain text first (stdlib only).
            file_bytes = _docx_to_text(file_path)
            mime_type = "text/plain"

        client = get_model_adapter(genai.Client())
        document = BinaryPart(
            data=file_bytes,
            mime_type=mime_type,
        )

        prompt = (
            "Extract all billing entitlements and financial terms from this contract document. "
            "Look for committed monthly minimums, included units, overage rates, promotional discounts, and annual escalators. "
            "If a value is not found, do not include it. Ensure provenance includes the exact quote from the document. "
            "Rules: (1) If an amendment or addendum changes a term (e.g. lowers the committed minimum), emit BOTH the "
            "original and the amended value as separate entitlements, each with its own effective_date. (2) For discounts "
            "and promotions, put the promo name in label, when it begins in start_date and when it ends in end_date; "
            "never in effective_date. (3) Emit included_units whenever the base fee 'includes' a quantity of units. "
            "(4) Only report overage_rate for a per-unit charge that applies ABOVE an included quantity; a per-unit "
            "list price that is simply billed per unit is not an overage rate. (5) provenance must be the verbatim clause text. "
            "(6) If overage pricing is tiered (different per-unit rates for different volume bands above the included "
            "quantity), emit one overage_tier entitlement per band with value = that band's per-unit rate and "
            "tier_up_to = the band's upper bound in units above the included quantity (null for the last band), "
            "instead of a single overage_rate. "
            "(7) Emit term_start and term_end for the initial term's start and end dates (value=0, date in "
            "effective_date). (8) Emit auto_renewal when the contract renews automatically (value = renewal "
            "term length in months, 0 if unstated) and renewal_notice_days for the notice period required to "
            "cancel before renewal. (9) For per-seat pricing, emit committed_seats (the seat/user/license "
            "count) and seat_price (the monthly price per seat)."
        )

        response = client.generate(
            model='gemini-2.5-flash',
            contents=[document, prompt],
            config=GenerationConfig(
                response_mime_type="application/json",
                response_schema=ContractEntitlements,
                temperature=0.0,
            ),
        )

        if response.parsed is not None:
            return ContractEntitlements.model_validate(response.parsed)
        if not response.text:
            return ContractEntitlements(customer_name="Unknown", entitlements=[])

        return ContractEntitlements.model_validate_json(response.text)
    except Exception as exc:
        logger.exception("document extraction failed for %s", file_path)
        raise UnreadableDocumentError() from exc


def extract_entitlements(file_path: str, *, client=None, model: str | None = None) -> ContractEntitlements:
    """Extract, page-anchor, and verify document entitlements."""
    visual = os.path.splitext(file_path)[1].lower() in {".pdf", ".png", ".jpg", ".jpeg"}
    if os.getenv("RECOUP_EXTRACTION_LEGACY") == "1" and not visual:
        return _extract_single_shot(file_path)
    try:
        from .extraction.extractor import extract_pages
        from .extraction.pages import MAX_SCANNED_PDF_PAGES, DocumentTooLargeError, load_pages
        from .extraction.verifier import verify
        from .extraction.ocr import get_ocr_adapter

        pages, source_kind = load_pages(file_path)
        if os.path.splitext(file_path)[1].lower() == ".pdf":
            try:
                native_pages = pdf_text_pages(file_path)
            except (OSError, ValueError, subprocess.SubprocessError):
                native_pages = []
            if native_pages_complete(file_path, native_pages, len(pages)):
                pages, source_kind = native_pages, "pdf_text"
            elif source_kind == "pdf_text":
                source_kind = "scanned"
        if source_kind == "scanned" and len(pages) > MAX_SCANNED_PDF_PAGES:
            raise UnreadableDocumentError()
        if source_kind in {"scanned", "image"}:
            suffix = os.path.splitext(file_path)[1].lower()
            mime_type = _MIME_OVERRIDES.get(suffix, "application/pdf" if suffix == ".pdf" else "image/jpeg")
            with open(file_path, "rb") as fh:
                pages = get_ocr_adapter(client=client).page_texts(fh.read(), mime_type)
            if not any(p.text.strip() for p in pages):
                raise UnreadableDocumentError()
        structural_verification = None
        if visual:
            try:
                issues = inspect_structure(pages)
                if not issues:
                    issues = inspect_raster(file_path, pages)
            except Exception as exc:
                raise LowConfidenceGateException([StructuralIssue(
                    0, "structural_check_unavailable",
                    "Visual structure could not be verified; inspect and re-upload this document.",
                )]) from exc
            if issues:
                raise LowConfidenceGateException(issues)
            structural_verification = {"state": "Verified", "version": 1, "pages": len(pages)}
        result = extract_pages(pages, source_kind, client=client, model=model,
                               file_name=os.path.basename(file_path))
        verified = verify(result, pages, client=client, model=model)
        entitlements = [Entitlement(**item.model_dump()) for item in verified]
        return ContractEntitlements(customer_name=result.customer_name, entitlements=entitlements,
                                    document=result.document.model_dump(),
                                    structural_verification=structural_verification)
    except DocumentTooLargeError:
        raise
    except UnreadableDocumentError:
        raise
    except LowConfidenceGateException:
        raise
    except Exception as exc:
        logger.exception("document extraction failed for %s", file_path)
        raise UnreadableDocumentError() from exc
