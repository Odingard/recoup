"""Deterministic id generation for Rights Graph entities.

Every entity id is a pure function of its identifying inputs — the same
contract + period always yields the same right/expected-state/discrepancy id.
Timestamps (generated_at, ingestion_timestamp) must never feed into ids.
"""
from __future__ import annotations

from hashlib import sha256


def stable_id(prefix: str, *parts) -> str:
    """`{prefix}_{sha256('|'.join(parts))[:20]}` — collision-safe, deterministic."""
    digest = sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:20]
    return f"{prefix}_{digest}"
