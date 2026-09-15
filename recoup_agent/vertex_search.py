"""Optional Vertex AI Search (Discovery Engine) RAG backend for clause retrieval.

If VERTEX_AI_SEARCH_ENGINE_ID is set, `lookup_contract_clause` queries the contract
corpus indexed in Vertex AI Search. If it is not set, or anything fails, the system
falls back to the local clause record - so the demo always runs.

Setup is documented in the README ("Vertex AI Search RAG upgrade").
Written against google-cloud-discoveryengine; could not be executed in the build
sandbox (no GCP), so verify the engine id / location for your project.
"""
from __future__ import annotations
from .cloud.search import get_clause_search, search_enabled


def is_enabled() -> bool:
    return search_enabled()


def search_clause(query: str) -> str | None:
    """Query the contract corpus; return the top extractive passage, or None on failure."""
    if not is_enabled():
        return None
    try:
        return get_clause_search().search(query)
    except Exception:
        return None
