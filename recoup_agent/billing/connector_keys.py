from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from google.api_core.exceptions import NotFound
from google.cloud import secretmanager

from .stripe_oauth import refresh_access_token


_client = None

_ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,255}$")


def _strip_prefix(raw: str | None, *, env_name: str) -> str | None:
    if not raw:
        return None
    value = raw.strip()
    prefix = f"{env_name}="
    if value.startswith(prefix):
        value = value[len(prefix):].strip()
    return value or None


def _project_id() -> str | None:
    return _strip_prefix(os.getenv("GOOGLE_CLOUD_PROJECT"), env_name="GOOGLE_CLOUD_PROJECT")


def _client_instance():
    global _client
    if _client is None:
        try:
            _client = secretmanager.SecretManagerServiceClient()
        except Exception:
            _client = None
    return _client


def _secret_name(account_id: str) -> str:
    if not _ACCOUNT_ID_RE.fullmatch(account_id):
        raise ValueError("Invalid account_id for connector secret name.")
    return f"recoup-connector-{account_id}"


def _is_valid_account_id(account_id: str | None) -> bool:
    return bool(account_id and _ACCOUNT_ID_RE.fullmatch(account_id))


def _secret_path(project_id: str, secret_id: str) -> str:
    return f"projects/{project_id}/secrets/{secret_id}"


def _get_secret_value(client, project_id: str, secret_id: str) -> str | None:
    try:
        response = client.access_secret_version(
            request={"name": f"{_secret_path(project_id, secret_id)}/versions/latest"}
        )
        return response.payload.data.decode("utf-8")
    except NotFound:
        return None
    except Exception:
        return None


def _store_secret_value(client, project_id: str, secret_id: str, value: str) -> None:
    secret_path = _secret_path(project_id, secret_id)
    try:
        client.create_secret(
            request={
                "parent": f"projects/{project_id}",
                "secret_id": secret_id,
                "secret": {
                    "replication": {"automatic": {}},
                },
            }
        )
    except Exception:
        pass
    client.add_secret_version(
        request={
            "parent": secret_path,
            "payload": {"data": value.encode("utf-8")},
        }
    )


def _load_connector_record(account_id: str | None) -> dict[str, Any] | str | None:
    if account_id is None:
        return None

    project_id = _project_id()
    client = _client_instance()
    if client is not None and project_id:
        try:
            secret_id = _secret_name(account_id)
            value = _get_secret_value(client, project_id, secret_id)
            if value:
                return value
        except ValueError:
            return None
        except Exception:
            pass

    return _strip_prefix(os.getenv("RECOUP_CONNECTOR_TEST_STRIPE_API_KEY"), env_name="RECOUP_CONNECTOR_TEST_STRIPE_API_KEY")


def _parse_record(value: dict[str, Any] | str | None) -> dict[str, Any] | str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    raw = value.strip()
    try:
        parsed = json.loads(raw)
    except Exception:
        return _strip_prefix(raw, env_name="RECOUP_CONNECTOR_TEST_STRIPE_API_KEY")
    if isinstance(parsed, dict):
        return parsed
    return _strip_prefix(raw, env_name="RECOUP_CONNECTOR_TEST_STRIPE_API_KEY")


def _refresh_if_needed(account_id: str, record: dict[str, Any]) -> dict[str, Any] | None:
    access_token = record.get("access_token")
    refresh_token = record.get("refresh_token")
    expires_at = record.get("expires_at")

    if access_token and expires_at:
        try:
            expiry = datetime.fromisoformat(str(expires_at))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if expiry > datetime.now(timezone.utc):
                return record
        except Exception:
            return record

    if access_token and not expires_at:
        return record

    if refresh_token:
        try:
            refreshed = refresh_access_token(str(refresh_token), account_id=account_id)
            store_connector_key(account_id, refreshed)
            return refreshed
        except Exception:
            return record if access_token else None

    return record if access_token else None


def resolve_connector_key(account_id: str | None) -> str | None:
    """Resolve the merchant-side read credential for a tenant.

    Sample/offline mode returns None. Otherwise we prefer the per-tenant Secret
    Manager credential, then fall back to the local development test credential.
    The resolved value is always the access token / API key that should be passed
    explicitly as ``api_key=``.
    """
    if account_id is None:
        return None

    record = _parse_record(_load_connector_record(account_id))
    if isinstance(record, dict):
        refreshed = _refresh_if_needed(account_id, record)
        if refreshed is None:
            return None
        if isinstance(refreshed, dict):
            return _strip_prefix(str(refreshed.get("access_token") or ""), env_name="access_token")
        return _strip_prefix(str(refreshed), env_name="access_token")

    if isinstance(record, str):
        return _strip_prefix(record, env_name="RECOUP_CONNECTOR_TEST_STRIPE_API_KEY")
    return None


def store_connector_key(account_id: str, key: str | dict[str, Any]) -> dict[str, Any]:
    """Persist the tenant's connector credential in Secret Manager.

    ``key`` may be a raw access token string or the structured OAuth credential
    payload returned from Stripe's OAuth exchange. Never raises.
    """
    project_id = _project_id()
    client = _client_instance()
    if not project_id or client is None:
        return {
            "status": "needs_review",
            "message": "Secret Manager is not available; could not store the connector credential.",
        }
    if not _is_valid_account_id(account_id):
        return {
            "status": "needs_review",
            "message": "Connector account id is invalid.",
        }

    try:
        if isinstance(key, dict):
            payload = {
                "kind": key.get("kind") or "stripe_oauth",
                "account_id": account_id,
                "access_token": _strip_prefix(str(key.get("access_token") or ""), env_name="access_token"),
                "refresh_token": _strip_prefix(str(key.get("refresh_token") or ""), env_name="refresh_token"),
                "stripe_account_id": key.get("stripe_account_id"),
                "scope": key.get("scope"),
                "token_type": key.get("token_type"),
                "livemode": key.get("livemode"),
                "expires_at": key.get("expires_at"),
                "stored_at": datetime.now(timezone.utc).isoformat(),
            }
            value = json.dumps(payload, sort_keys=True)
        else:
            value = _strip_prefix(key, env_name="RECOUP_CONNECTOR_TEST_STRIPE_API_KEY") or ""
        if not value:
            return {"status": "needs_review", "message": "The connector credential is empty."}
        secret_id = _secret_name(account_id)
        _store_secret_value(client, project_id, secret_id, value)
        return {
            "status": "success",
            "message": "Connector credential stored securely.",
            "secret_name": _secret_name(account_id),
            "stripe_account_id": key.get("stripe_account_id") if isinstance(key, dict) else None,
        }
    except ValueError as exc:
        return {
            "status": "needs_review",
            "message": f"Could not store connector credential securely. Details: {exc}",
        }
    except Exception as exc:
        return {
            "status": "needs_review",
            "message": f"Could not store connector credential securely. Details: {exc}",
        }


def get_connector_status(account_id: str | None) -> dict[str, Any]:
    if account_id is None:
        return {
            "connected": False,
            "status": "sample_mode",
            "message": "Sample mode runs without a Stripe connection.",
        }
    if not _is_valid_account_id(account_id):
        return {
            "connected": False,
            "status": "needs_review",
            "message": "Connector account id is invalid.",
        }

    record = _parse_record(_load_connector_record(account_id))
    if record is None:
        return {
            "connected": False,
            "status": "not_connected",
            "message": "Stripe App not connected yet.",
        }
    if isinstance(record, dict):
        access_token = record.get("access_token")
        return {
            "connected": bool(access_token),
            "status": "connected" if access_token else "needs_review",
            "message": "Stripe App is connected." if access_token else "Connector credential is incomplete.",
            "stripe_account_id": record.get("stripe_account_id"),
            "livemode": record.get("livemode"),
            "expires_at": record.get("expires_at"),
        }
    return {
        "connected": bool(record),
        "status": "connected" if record else "needs_review",
        "message": "Stripe App is connected." if record else "Connector credential is missing.",
    }
