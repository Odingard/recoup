"""Startup config checks and the /api/ready dependency probe.

Reports only names and booleans — never secret values or fragments.
"""
from __future__ import annotations

import os
import concurrent.futures


def _sample_mode() -> bool:
    return os.getenv("RECOUP_SAMPLE_MODE", "").lower() in {"1", "true", "yes", "on"}


def production_mode() -> bool:
    return not _sample_mode()


def _env_set(name: str) -> bool:
    return bool((os.getenv(name) or "").strip())


def config_problems() -> list[str]:
    """Actionable misconfiguration messages (non-secret names only)."""
    problems = []
    if not _env_set("GOOGLE_CLOUD_PROJECT"):
        problems.append(
            "Set GOOGLE_CLOUD_PROJECT to the GCP project id (Firestore + "
            "Firebase depend on it).")
    # RECOUP_GIT_SHA is reported as a warning only.
    return problems


def _check_firestore() -> dict:
    from . import db
    try:
        client = db.get_client()

        def _probe():
            client.collection("_readiness").document("probe").get()

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(_probe).result(timeout=5)
        return {"ok": True, "detail": "firestore reachable"}
    except Exception as exc:
        return {"ok": False, "detail": f"firestore check failed: {type(exc).__name__}"}


def _check_firebase() -> dict:
    try:
        from .api import _ensure_firebase_app  # late import: api -> readiness
        _ensure_firebase_app()
        return {"ok": True, "detail": "firebase app initializable"}
    except Exception as exc:
        return {"ok": False, "detail": f"firebase auth failed: {type(exc).__name__}"}


def dependency_checks(deep: bool) -> dict[str, dict]:
    """Per-check {"ok", "detail"}; deep=False skips network/app probes."""
    checks: dict[str, dict] = {
        "project_config": {
            "ok": _env_set("GOOGLE_CLOUD_PROJECT"),
            "detail": "GOOGLE_CLOUD_PROJECT set" if _env_set("GOOGLE_CLOUD_PROJECT")
                      else "GOOGLE_CLOUD_PROJECT missing",
        },
        "billing_key_present": {
            "ok": _env_set("RECOUP_BILLING_STRIPE_API_KEY"),
            "detail": "RECOUP_BILLING_STRIPE_API_KEY "
                      + ("set" if _env_set("RECOUP_BILLING_STRIPE_API_KEY") else "missing"),
        },
        "connector_key_present": {
            "ok": _env_set("RECOUP_CONNECTOR_TEST_STRIPE_API_KEY")
                  or _env_set("RECOUP_STRIPE_APP_CLIENT_ID"),
            "detail": "connector credential configured"
                      if (_env_set("RECOUP_CONNECTOR_TEST_STRIPE_API_KEY")
                          or _env_set("RECOUP_STRIPE_APP_CLIENT_ID"))
                      else "no connector credential configured",
        },
    }
    provider = os.getenv("RECOUP_MODEL_PROVIDER", "google").strip().lower()
    if provider == "google":
        configured = _env_set("GOOGLE_GENAI_USE_VERTEXAI") and _env_set("GOOGLE_CLOUD_LOCATION")
        checks["vertex_config"] = {
            "ok": configured,
            "detail": "vertex env configured" if configured
                      else "GOOGLE_GENAI_USE_VERTEXAI/GOOGLE_CLOUD_LOCATION missing",
        }
    else:
        configured = provider == "remote" and _env_set("RECOUP_MODEL_URL") and _env_set("RECOUP_REMOTE_MODEL")
        checks["model_config"] = {
            "ok": configured,
            "detail": "remote model configured" if configured
                      else "model provider disabled, unsupported or missing configuration",
        }
    if _env_set("RECOUP_GIT_SHA"):
        checks["version_stamped"] = {"ok": True, "detail": "RECOUP_GIT_SHA set"}
    else:
        checks["version_stamped"] = {"ok": True, "detail": "warning: RECOUP_GIT_SHA missing"}
    if deep and not _sample_mode():
        checks["firestore"] = _check_firestore()
        checks["firebase_auth"] = _check_firebase()
    elif deep:
        checks["firestore"] = {"ok": True, "detail": "skipped in sample mode"}
        checks["firebase_auth"] = {"ok": True, "detail": "skipped in sample mode"}
    else:
        checks["firestore"] = {"ok": True, "detail": "not probed"}
        checks["firebase_auth"] = {"ok": True, "detail": "not probed"}
    return checks
