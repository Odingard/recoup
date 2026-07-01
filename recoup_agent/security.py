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
    """Fail closed if the write (billing) key and the read-only connector key are
    the same value. That overlap is the one that would let a key used to *read* a
    customer's Stripe also *write* — it must never happen.

    A single full-access key MAY be used for billing on its own (e.g. before a
    dedicated restricted key exists); the connector always resolves its own
    per-tenant read-only key, so reusing the legacy value for billing alone is
    allowed.
    """
    billing = _env_value("RECOUP_BILLING_STRIPE_API_KEY")
    connector_test = _env_value("RECOUP_CONNECTOR_TEST_STRIPE_API_KEY")

    if billing and connector_test and billing == connector_test:
        raise RuntimeError(
            "Recoup's billing (write) key and connector (read-only) key must be "
            "different values. Refusing to start."
        )
