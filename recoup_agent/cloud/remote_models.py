from __future__ import annotations

import json
import os
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .models import GenerationConfig, ModelResponse, ProviderConfigurationError


class ModelRequestError(RuntimeError):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"Model request failed ({status_code}).")


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RemoteModelAdapter:
    """Text-only Chat Completions endpoint with JSON-schema output."""

    def __init__(self):
        self.url = os.getenv("RECOUP_MODEL_URL", "").rstrip("/")
        self.model = os.getenv("RECOUP_REMOTE_MODEL", "").strip()
        url = urlparse(self.url)
        if url.scheme not in {"https", "http"} or not url.hostname or not self.model:
            raise ProviderConfigurationError("Set RECOUP_MODEL_URL and RECOUP_REMOTE_MODEL.")
        if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ProviderConfigurationError("Remote model endpoints require HTTPS.")
        if url.username or url.password or url.query or url.fragment:
            raise ProviderConfigurationError("Model URL must not contain credentials, query or fragment.")
        self.opener = build_opener(NoRedirects())

    def generate(self, *, model, contents, config: GenerationConfig):
        if not isinstance(contents, str):
            raise ProviderConfigurationError("Remote models accept text only; use local or Document AI OCR.")
        if config.cached_content:
            raise ProviderConfigurationError("Remote model caching is unavailable.")
        messages = []
        if config.system_instruction:
            messages.append({"role": "system", "content": config.system_instruction})
        messages.append({"role": "user", "content": contents})
        payload = {
            "model": self.model, "messages": messages, "temperature": config.temperature,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": config.response_schema.__name__,
                "schema": config.response_schema.model_json_schema(),
            }},
        }
        headers = {"Content-Type": "application/json"}
        key = os.getenv("RECOUP_MODEL_API_KEY", "").removeprefix("RECOUP_MODEL_API_KEY=").strip()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        request = Request(self.url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        try:
            with self.opener.open(request, timeout=int(os.getenv("RECOUP_EXTRACTION_TIMEOUT_MS", "120000")) / 1000) as response:
                body = json.load(response)
        except HTTPError as exc:
            raise ModelRequestError(exc.code) from None
        choices = body["choices"]
        if len(choices) != 1 or choices[0]["finish_reason"] != "stop":
            raise ValueError("Remote model response was incomplete.")
        message = choices[0]["message"]
        if message.get("refusal"):
            raise ValueError("Remote model declined the request.")
        parsed = config.response_schema.model_validate_json(message["content"])
        return ModelResponse(parsed=parsed)

    def create_cache(self, *, model, text, system, ttl):
        raise ProviderConfigurationError("Remote model caching is unavailable; use chunked extraction.")

    def delete_cache(self, name):
        pass
