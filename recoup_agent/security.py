from __future__ import annotations

import os


def _strip_prefix(raw: str | None, *, env_name: str) -> str | None:
    if not raw:
        return None
    value = raw.strip()
    prefix = f"{env_name}="
    if value.startswith(prefix):
        value = value[len(prefix):].strip()
    return value or None


def _env_value(name: str) -> str | None:
    return _strip_prefix(os.getenv(name), env_name=name)


def assert_key_separation() -> None:
    billing = _env_value("RECOUP_BILLING_STRIPE_API_KEY")
    legacy = _env_value("STRIPE_API_KEY") or _env_value("STRIPE")
    connector_test = _env_value("RECOUP_CONNECTOR_TEST_STRIPE_API_KEY")

    if billing and legacy and billing == legacy:
        raise RuntimeError("Recoup billing key must be separate from legacy Stripe env keys.")
    if billing and connector_test and billing == connector_test:
        raise RuntimeError("Recoup billing key must be separate from the connector test key.")
    if legacy and connector_test and legacy == connector_test:
        raise RuntimeError("Legacy Stripe env keys must not be reused for the connector test key.")
