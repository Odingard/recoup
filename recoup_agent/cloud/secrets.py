from __future__ import annotations

import os
from importlib import import_module
from typing import Protocol

from .models import ProviderConfigurationError


class SecretStore(Protocol):
    def get(self, secret_id: str) -> str | None: ...
    def put(self, secret_id: str, value: str) -> None: ...
    def delete(self, secret_id: str) -> bool: ...


def get_secret_store() -> SecretStore | None:
    provider = os.getenv("RECOUP_SECRET_PROVIDER", "google").strip().lower()
    if provider == "disabled":
        return None
    if provider != "google":
        raise ProviderConfigurationError(f"Unsupported RECOUP_SECRET_PROVIDER: {provider}")
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "").removeprefix("GOOGLE_CLOUD_PROJECT=").strip()
    if not project:
        return None
    return import_module(".google_secrets", __package__).GoogleSecretStore(project)
