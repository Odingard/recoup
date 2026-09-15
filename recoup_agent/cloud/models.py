from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol, runtime_checkable

from pydantic import BaseModel


@dataclass(frozen=True)
class BinaryPart:
    data: bytes
    mime_type: str


@dataclass(frozen=True)
class GenerationConfig:
    response_schema: type[BaseModel]
    system_instruction: str | None = None
    cached_content: str | None = None
    temperature: float = 0.0
    response_mime_type: str = "application/json"


@dataclass(frozen=True)
class ModelResponse:
    text: str | None = None
    parsed: BaseModel | dict | list | None = None


@runtime_checkable
class ModelAdapter(Protocol):
    def generate(self, *, model: str, contents: str | list[str | BinaryPart],
                 config: GenerationConfig) -> ModelResponse: ...
    def create_cache(self, *, model: str, text: str, system: str, ttl: str) -> str: ...
    def delete_cache(self, name: str) -> None: ...


class ProviderConfigurationError(ValueError):
    pass


def get_model_adapter(client=None) -> ModelAdapter:
    if isinstance(client, ModelAdapter):
        return client
    # Existing callers can continue injecting a Gemini-shaped test client.
    provider = "google" if client is not None else os.getenv("RECOUP_MODEL_PROVIDER", "google").strip().lower()
    if provider == "google":
        return import_module(".google_models", __package__).GoogleModelAdapter(client=client)
    if provider == "remote":
        return import_module(".remote_models", __package__).RemoteModelAdapter()
    if provider == "disabled":
        return DisabledModelAdapter()
    raise ProviderConfigurationError(f"Unsupported RECOUP_MODEL_PROVIDER: {provider}")


class DisabledModelAdapter:
    def generate(self, *, model, contents, config):
        raise ProviderConfigurationError("Model extraction is disabled; configure RECOUP_MODEL_PROVIDER.")

    def create_cache(self, *, model, text, system, ttl):
        raise ProviderConfigurationError("Model caching is disabled.")

    def delete_cache(self, name):
        pass


# Compatibility for the legacy ingestion client factory.
Client = get_model_adapter
