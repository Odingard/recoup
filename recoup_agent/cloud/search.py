import os
from importlib import import_module
from typing import Protocol

from .models import ProviderConfigurationError


class ClauseSearch(Protocol):
    def search(self, query: str) -> str | None: ...


class LocalClauseSearch:
    def search(self, query: str) -> str | None:
        return None


def search_enabled() -> bool:
    provider = os.getenv("RECOUP_SEARCH_PROVIDER", "auto").strip().lower()
    if provider == "auto":
        return bool(os.getenv("VERTEX_AI_SEARCH_ENGINE_ID", "").strip())
    if provider not in {"google", "local"}:
        raise ProviderConfigurationError(f"Unsupported RECOUP_SEARCH_PROVIDER: {provider}")
    return provider == "google"


def get_clause_search() -> ClauseSearch:
    if not search_enabled():
        return LocalClauseSearch()
    return import_module(".google_search", __package__).GoogleClauseSearch()
