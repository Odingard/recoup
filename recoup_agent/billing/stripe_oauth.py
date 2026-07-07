from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any


DEFAULT_AUTHORIZE_URL = "https://marketplace.stripe.com/oauth/v2/authorize"
DEFAULT_TOKEN_URL = "https://api.stripe.com/v1/oauth/token"
DEFAULT_REDIRECT_URI = "https://recoup.odingard.com/api/connector/stripe/oauth/callback"
DEFAULT_WEB_BASE_URL = "https://recoup.odingard.com"


def _strip_prefix(raw: str | None, *, env_name: str) -> str | None:
    if not raw:
        return None
    value = raw.strip()
    prefix = f"{env_name}="
    if value.startswith(prefix):
        value = value[len(prefix):].strip()
    return value or None


def _env_value(name: str, default: str | None = None) -> str | None:
    return _strip_prefix(os.getenv(name, default), env_name=name)


def oauth_client_id() -> str | None:
    return _env_value("RECOUP_STRIPE_APP_CLIENT_ID")


def oauth_client_secret() -> str | None:
    return _env_value("RECOUP_STRIPE_APP_SECRET")


def oauth_authorize_url() -> str:
    return _env_value("RECOUP_STRIPE_APP_AUTHORIZE_URL", DEFAULT_AUTHORIZE_URL) or DEFAULT_AUTHORIZE_URL


def oauth_token_url() -> str:
    return _env_value("RECOUP_STRIPE_APP_TOKEN_URL", DEFAULT_TOKEN_URL) or DEFAULT_TOKEN_URL


def oauth_redirect_uri() -> str:
    return _env_value("RECOUP_STRIPE_APP_REDIRECT_URI", DEFAULT_REDIRECT_URI) or DEFAULT_REDIRECT_URI


def oauth_web_base_url() -> str:
    return _env_value("RECOUP_WEB_BASE_URL", DEFAULT_WEB_BASE_URL) or DEFAULT_WEB_BASE_URL


def _state_secret() -> str | None:
    return _env_value("RECOUP_STRIPE_APP_STATE_SECRET") or oauth_client_secret()


def _urlsafe_b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _urlsafe_b64decode(data: str) -> bytes:
    padding = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign_state(payload: bytes) -> str:
    secret = _state_secret()
    if not secret:
        raise RuntimeError("Stripe OAuth state secret is not configured.")
    sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return _urlsafe_b64encode(sig)


def build_oauth_state(account_id: str, uid: str, *, email: str | None = None, install_mode: str = "test") -> str:
    payload = {
        "account_id": account_id,
        "email": email,
        "iat": int(time.time()),
        "install_mode": install_mode,
        "uid": uid,
    }
    encoded = _urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{encoded}.{_sign_state(encoded.encode('utf-8'))}"


def parse_oauth_state(state: str, *, max_age_seconds: int = 900) -> dict[str, Any]:
    if not state or "." not in state:
        raise ValueError("Invalid Stripe OAuth state.")
    encoded, signature = state.split(".", 1)
    expected = _sign_state(encoded.encode("utf-8"))
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Stripe OAuth state signature mismatch.")
    payload = json.loads(_urlsafe_b64decode(encoded).decode("utf-8"))
    issued_at = int(payload.get("iat") or 0)
    if not issued_at or int(time.time()) - issued_at > max_age_seconds:
        raise ValueError("Stripe OAuth state has expired.")
    return payload


def build_oauth_install_url(*, state: str, redirect_uri: str | None = None, client_id: str | None = None) -> str:
    redirect_uri = redirect_uri or oauth_redirect_uri()
    client_id = client_id or oauth_client_id()
    if not client_id:
        raise RuntimeError("RECOUP_STRIPE_APP_CLIENT_ID is not configured.")
    params = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"{oauth_authorize_url()}?{params}"


def _post_form(url: str, data: dict[str, str], *, secret: str | None = None) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    if secret:
        basic = base64.b64encode(f"{secret}:".encode("utf-8")).decode("utf-8")
        request.add_header("Authorization", f"Basic {basic}")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(raw or f"Stripe OAuth request failed with HTTP {exc.code}") from exc
    except Exception as exc:
        raise RuntimeError(f"Stripe OAuth request failed: {exc}") from exc


def _normalize_token_response(payload: dict[str, Any], *, account_id: str) -> dict[str, Any]:
    expires_at = None
    expires_in = payload.get("expires_in")
    if expires_in is not None:
        try:
            expires_at = datetime.fromtimestamp(time.time() + int(expires_in), tz=timezone.utc).isoformat()
        except Exception:
            expires_at = None
    stripe_account_id = payload.get("stripe_user_id") or payload.get("account_id")
    return {
        "kind": "stripe_oauth",
        "account_id": account_id,
        "access_token": payload.get("access_token"),
        "refresh_token": payload.get("refresh_token"),
        "token_type": payload.get("token_type"),
        "scope": payload.get("scope"),
        "livemode": payload.get("livemode"),
        "stripe_account_id": stripe_account_id,
        "expires_at": expires_at,
    }


def exchange_authorization_code(code: str, *, account_id: str) -> dict[str, Any]:
    secret = oauth_client_secret()
    if not secret:
        raise RuntimeError("RECOUP_STRIPE_APP_SECRET is not configured.")
    payload = _post_form(
        oauth_token_url(),
        {
            "code": code,
            "grant_type": "authorization_code",
        },
        secret=secret,
    )
    return _normalize_token_response(payload, account_id=account_id)


def refresh_access_token(refresh_token: str, *, account_id: str) -> dict[str, Any]:
    secret = oauth_client_secret()
    if not secret:
        raise RuntimeError("RECOUP_STRIPE_APP_SECRET is not configured.")
    payload = _post_form(
        oauth_token_url(),
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        secret=secret,
    )
    refreshed = _normalize_token_response(payload, account_id=account_id)
    refreshed["refresh_token"] = refreshed.get("refresh_token") or refresh_token
    return refreshed
