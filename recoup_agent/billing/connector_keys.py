from __future__ import annotations

import os
from typing import Any


def _strip_prefix(raw: str | None, *, env_name: str) -> str | None:
    if not raw:
        return None
    value = raw.strip()
    prefix = f"{env_name}="
    if value.startswith(prefix):
        value = value[len(prefix):].strip()
    return value or None


def _project_id() -> str | None:
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    if project:
        return project
    try:
        import google.auth

        _, detected = google.auth.default()
        return detected
    except Exception:
        return None


def _secret_name(account_id: str) -> str:
    return f"recoup-connector-{account_id}"


def _client():
    try:
        from google.cloud import secretmanager

        return secretmanager.SecretManagerServiceClient()
    except Exception:
        return None


def _latest_secret_value(client, project_id: str, secret_id: str) -> str | None:
    try:
        name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
        response = client.access_secret_version(request={"name": name})
        payload = getattr(response, "payload", None)
        data = getattr(payload, "data", b"") if payload is not None else b""
        if not data:
            return None
        return data.decode("utf-8").strip() or None
    except Exception:
        return None


def resolve_connector_key(account_id: str | None) -> str | None:
    if account_id is None:
        return None

    project_id = _project_id()
    client = _client()
    if client is not None and project_id:
        secret_id = _secret_name(account_id)
        try:
            value = _latest_secret_value(client, project_id, secret_id)
            if value:
                return _strip_prefix(value, env_name=secret_id)
        except Exception:
            pass

    return _strip_prefix(os.getenv("RECOUP_CONNECTOR_TEST_STRIPE_API_KEY"), env_name="RECOUP_CONNECTOR_TEST_STRIPE_API_KEY")


def store_connector_key(account_id: str, key: str) -> dict[str, Any]:
    client = _client()
    project_id = _project_id()
    if client is None or not project_id:
        return {
            "status": "needs_config",
            "message": "Secret Manager is not available; cannot store connector key.",
        }

    secret_id = _secret_name(account_id)
    parent = f"projects/{project_id}"
    normalized_key = _strip_prefix(key, env_name=secret_id) or key.strip()

    try:
        try:
            client.create_secret(
                request={
                    "parent": parent,
                    "secret_id": secret_id,
                    "secret": {"replication": {"automatic": {}}},
                }
            )
        except Exception:
            pass

        client.add_secret_version(
            request={
                "parent": f"{parent}/secrets/{secret_id}",
                "payload": {"data": normalized_key.encode("utf-8")},
            }
        )
        return {
            "status": "success",
            "message": "Connector key stored in Secret Manager.",
            "secret_name": f"projects/{project_id}/secrets/{secret_id}",
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Could not store connector key: {exc}",
        }
