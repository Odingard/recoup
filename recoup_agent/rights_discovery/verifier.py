"""Verification step: a fresh LLM call independently assesses one candidate.

Separate generate_content call with no shared context — the candidate is
treated as untrusted input inside <candidate> tags.
"""
from __future__ import annotations

import json

from ..reconciliation import CONFIDENCE_THRESHOLD
from .discovery import DEFAULT_MODEL, _default_client, generate
from .models import CandidateFinancialRight, CandidateStatus
from .prompts import VERIFICATION_SYSTEM, VerificationOutput


def verify_candidate_right(candidate: CandidateFinancialRight,
                           document_text: str, *,
                           client=None,
                           model: str = DEFAULT_MODEL) -> CandidateFinancialRight:
    client = client or _default_client()
    if candidate.status != CandidateStatus.discovered.value:
        return candidate  # nothing to verify on already-failed candidates
    contents = (f"<document>\n{document_text}\n</document>\n"
                f"<candidate>\n{json.dumps(candidate.to_dict(), default=str)}\n</candidate>")
    out: VerificationOutput = generate(client, VERIFICATION_SYSTEM, contents,
                                       VerificationOutput, model)
    candidate.verification_confidence = out.confidence
    candidate.verification_model = model
    candidate.metadata["verification"] = out.model_dump()

    if not out.is_financial_right:
        candidate.status = CandidateStatus.rejected.value
        candidate.rejection_reason = "verifier: not a financial right"
        return candidate
    if not out.quote_supported:
        candidate.status = CandidateStatus.rejected.value
        candidate.rejection_reason = "verifier: source quote not supported by document"
        return candidate

    all_checks = (out.is_financial_right and out.quote_supported
                  and out.holder_obligor_correct and out.trigger_supported
                  and out.calculation_supported and out.dates_present
                  and out.observations_identifiable)
    reasons = []
    if out.invented_terms:
        reasons.append(f"invented terms: {', '.join(out.invented_terms)}")
    if out.ambiguity_requires_review:
        reasons.append("ambiguity requires human review")
    for flag, label in ((out.holder_obligor_correct, "holder/obligor"),
                        (out.trigger_supported, "trigger"),
                        (out.calculation_supported, "calculation"),
                        (out.dates_present, "dates"),
                        (out.observations_identifiable, "observations")):
        if not flag:
            reasons.append(f"{label} not supported by the document")
    if (candidate.discovery_confidence or 0) < CONFIDENCE_THRESHOLD:
        reasons.append(f"discovery confidence {candidate.discovery_confidence:.2f} "
                       f"below {CONFIDENCE_THRESHOLD}")
    if (out.confidence or 0) < CONFIDENCE_THRESHOLD:
        reasons.append(f"verification confidence {out.confidence:.2f} "
                       f"below {CONFIDENCE_THRESHOLD}")

    if all_checks and not reasons:
        candidate.status = CandidateStatus.verified.value
    else:
        candidate.status = CandidateStatus.needs_review.value
        candidate.rejection_reason = "; ".join(reasons) or out.notes or None
    return candidate
