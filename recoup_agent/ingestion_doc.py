import mimetypes
import os
from typing import List, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

class Entitlement(BaseModel):
    term_type: str = Field(description="The type of entitlement, e.g., 'committed_minimum', 'overage_rate', 'discount', 'escalator'")
    value: float = Field(description="The numeric value of the entitlement. For percentages, use decimals (e.g. 0.05 for 5%).")
    label: Optional[str] = Field(None, description="Short human label for this term as it might appear on an invoice line, e.g. 'Launch promo', 'Volume discount', 'Amendment 1'.")
    effective_date: Optional[str] = Field(None, description="For committed_minimum/overage_rate/escalator terms: the date this value takes effect (ISO YYYY-MM-DD). For an amendment that changes a term, emit a SEPARATE entitlement with the amendment's effective date. Do NOT use this field for discount start or end dates.")
    start_date: Optional[str] = Field(None, description="For discounts/promotions: the date the discount STARTS (ISO YYYY-MM-DD). Leave null otherwise.")
    end_date: Optional[str] = Field(None, description="For discounts/promotions: the date the discount ENDS or expires (ISO YYYY-MM-DD). Leave null if it never expires.")
    confidence_score: float = Field(description="Confidence score of this extraction between 0.0 and 1.0")
    provenance: str = Field(description="The exact clause quote and page number indicating where this was found.")

class ContractEntitlements(BaseModel):
    customer_name: str = Field(description="The name of the customer the contract is with.")
    entitlements: List[Entitlement]

def extract_entitlements(file_path: str) -> ContractEntitlements:
    """Extracts structured billing entitlements from a document of any format."""
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = "application/octet-stream"
        if file_path.endswith('.md') or file_path.endswith('.txt'):
            mime_type = "text/plain"
        elif file_path.endswith('.pdf'):
            mime_type = "application/pdf"

    # Assume we use vertex based on the environment variables defined in README
    client = genai.Client()
    
    with open(file_path, "rb") as f:
        file_bytes = f.read()

    document = types.Part.from_bytes(
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
        "list price that is simply billed per unit is not an overage rate. (5) provenance must be the verbatim clause text."
    )

    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=[document, prompt],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ContractEntitlements,
            temperature=0.0,
        ),
    )

    if not response.text:
        return ContractEntitlements(customer_name="Unknown", entitlements=[])

    return ContractEntitlements.model_validate_json(response.text)
