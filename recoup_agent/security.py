from __future__ import annotations

import os


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    if not value:
        return None
    value = value.strip()
    prefix = f"{name}="
    if value.startswith(prefix):
        value = value[len(prefix):].strip()
    return value or None


def assert_key_separation() -> None:
    """Fail closed when the billing key overlaps with the connector test key."""
    billing = _env_value("RECOUP_BILLING_STRIPE_API_KEY")
    connector_test = _env_value("RECOUP_CONNECTOR_TEST_STRIPE_API_KEY")

    if billing and connector_test and billing == connector_test:
        raise RuntimeError(
            "Recoup's billing (write) key and connector (read-only) key must be different values. Refusing to start."
        )
