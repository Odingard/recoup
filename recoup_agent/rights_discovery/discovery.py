"""Discovery step: LLM proposes candidate financial rights from a document.

The document is untrusted data wrapped in <document> tags; candidates whose
source_quote cannot be found verbatim (whitespace-normalized) in the document
are created as `unsupported` — provenance is enforced, not trusted.
"""
from __future__ import annotations

import json
import os
import re

from ..rights_graph.ids import stable_id
from .models import CandidateFinancialRight, CandidateStatus
from .prompts import DISCOVERY_SYSTEM, DiscoveryOutput

DEFAULT_MODEL = "gemini-2.5-flash"


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _default_client():
    from google import genai
    return genai.Client()


def document_text_from_file(path: str) -> tuple[str | None, str | None]:
    """-> (text, error). text/markdown and .docx supported; PDF is skipped with
    a clear reason (PDF extraction lives on the legacy ingest path)."""
    import os.path
    from ..ingestion_doc import _docx_to_text
    suffix = os.path.splitext(path)[1].lower()
    if suffix in {".txt", ".md"}:
        with open(path, "rb") as fh:
            return fh.read().decode("utf-8", errors="replace"), None
    if suffix == ".docx":
        return _docx_to_text(path).decode("utf-8", errors="replace"), None
    if suffix == ".pdf":
        return None, ("PDF discovery needs the document's text layer; the "
                      "legacy ingest path handles PDFs — upload .txt/.md/.docx "
                      "for rights discovery.")
    return None, f"Unsupported document type '{suffix}' for rights discovery."


def _call(client, system: str, contents: str, schema, model: str):
    import time
    from google.genai import types as genai_types
    last_exc = None
    for attempt in range(4):
        try:
            resp = client.models.generate_content(
                model=model,
                contents=contents,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system,
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=0.0,
                ),
            )
            return schema.model_validate_json(resp.text)
        except Exception as exc:  # transient quota/demand errors retry; permanent ones fail
            last_exc = exc
            status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
            if status not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
    raise last_exc


def _parse_structured(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def discover_financial_rights(document_text: str, source_context: dict, *,
                              client=None, model: str = DEFAULT_MODEL
                              ) -> list[CandidateFinancialRight]:
    """-> candidate rights (status=discovered, or unsupported when the quoted
    provenance is absent from the document)."""
    account_id = source_context.get("account_id")
    source_id = source_context.get("source_id") or "document"
    client = client or _default_client()
    output = _call(client, DISCOVERY_SYSTEM,
                   f"<document>\n{document_text}\n</document>",
                   DiscoveryOutput, model)
    candidates: list[CandidateFinancialRight] = []
    doc_norm = _norm_ws(document_text)
    anomalies = output.document_anomalies or []
    for r in output.rights or []:
        cid = stable_id("cand", account_id, source_id, r.right_family,
                        r.source_quote)
        meta = {"document_anomalies": anomalies,
                "customer_name": source_context.get("customer_name"),
                "ambiguities": r.ambiguities,
                "constants": [c.model_dump() for c in (r.constants or [])],
                "actual_observation": r.actual_observation}
        quote_found = bool(r.source_quote) and _norm_ws(r.source_quote) in doc_norm
        calc = _parse_structured(r.calculation_structured)
        if calc is None and r.calculation_type not in (None, "none", "unsupported"):
            calc = {"type": r.calculation_type}
        elif calc is not None:
            calc.setdefault("type", r.calculation_type)
        else:
            calc = {"type": r.calculation_type or "unsupported"}
        candidates.append(CandidateFinancialRight(
            candidate_id=cid,
            account_id=account_id,
            source_id=source_id,
            holder_party_id=r.holder_party or None,
            obligor_party_id=r.obligor_party or None,
            right_name=r.right_name,
            right_family=r.right_family or "unknown",
            description=r.description,
            trigger_spec=_parse_structured(r.trigger_structured),
            calculation_spec=calc,
            required_observations=list(r.required_observations or []),
            effective_from=r.effective_from,
            effective_until=r.effective_until,
            evidence_refs=[stable_id("ev", source_id, r.source_quote)]
            if r.source_quote else [],
            source_quote=r.source_quote,
            discovery_confidence=r.confidence or 0.0,
            discovery_model=model,
            status=(CandidateStatus.discovered.value if quote_found
                    else CandidateStatus.unsupported.value),
            rejection_reason=None if quote_found
            else "provenance not found in source",
            metadata=meta,
        ))
    return candidates


def generate(client, system: str, contents: str, schema, model: str):
    """Shared structured call (used by verifier/investigation/strategist)."""
    return _call(client, system, contents, schema, model)
