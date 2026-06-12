import mimetypes
import os
from typing import List, Optional
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

class Entitlement(BaseModel):
    term_type: str = Field(description="The type of entitlement, e.g., 'committed_minimum', 'overage_rate', 'discount', 'escalator'")
    value: float = Field(description="The numeric value of the entitlement. For percentages, use decimals (e.g. 0.05 for 5%).")
    effective_date: Optional[str] = Field(None, description="The effective date or expiry date if applicable (ISO format YYYY-MM-DD).")
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
        "If a value is not found, do not include it. Ensure provenance includes the exact quote from the document."
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
