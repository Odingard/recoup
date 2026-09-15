from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from google import genai
from google.genai import types
from pydantic import BaseModel

from .models import BinaryPart, GenerationConfig, ModelResponse


@runtime_checkable
class ParsedResponse(Protocol):
    parsed: BaseModel | dict | list | None


class GoogleModelAdapter:
    def __init__(self, client=None):
        self.client = client if client is not None else genai.Client(
            http_options=types.HttpOptions(
                timeout=int(os.getenv("RECOUP_EXTRACTION_TIMEOUT_MS", "120000"))))

    def generate(self, *, model, contents, config):
        if isinstance(config, GenerationConfig):
            config = types.GenerateContentConfig(
                response_schema=config.response_schema,
                response_mime_type=config.response_mime_type,
                system_instruction=config.system_instruction,
                cached_content=config.cached_content,
                temperature=config.temperature)
        if isinstance(contents, list):
            contents = [types.Part.from_bytes(data=part.data, mime_type=part.mime_type)
                        if isinstance(part, BinaryPart) else part for part in contents]
        response = self.client.models.generate_content(
            model=model, contents=contents, config=config)
        if isinstance(response, ParsedResponse) and response.parsed is not None:
            return ModelResponse(parsed=response.parsed)
        return ModelResponse(text=response.text)

    def create_cache(self, *, model, text, system, ttl):
        cache = self.client.caches.create(model=model, config=types.CreateCachedContentConfig(
            contents=[text], system_instruction=system, ttl=ttl))
        return cache.name

    def delete_cache(self, name):
        self.client.caches.delete(name=name)
