"""Customer identity resolution across contract documents and billing/usage
exports, using exact normalized labels first and unambiguous suffix stripping
as a deliberately narrow fallback."""
from __future__ import annotations

import re

SUFFIXES = {"llc", "inc", "co", "corp", "corporation", "company", "group", "grp",
            "ltd", "limited", "plc", "gmbh", "the", "and", "&"}
ABBREVIATIONS = {"intl": "international", "svcs": "services", "mfg": "manufacturing",
                 "tech": "technology", "assoc": "associates", "bros": "brothers"}


def _tokens(name: str) -> list[str]:
    raw = re.sub(r"[^a-z0-9]+", " ", str(name or "").lower())
    return [ABBREVIATIONS.get(t, t) for t in raw.split()]


def normalized_key(name: str) -> str:
    """Normalize case, punctuation, whitespace, and known abbreviations while
    keeping corporate suffixes."""
    return "_".join(_tokens(name))


def canonical_key(name: str) -> str:
    """Compatibility key with corporate suffixes removed."""
    return "_".join(t for t in _tokens(name or "") if t not in SUFFIXES)


class CustomerResolver:
    """Maps a billing/usage label to a contract customer_id."""

    def __init__(self, contracts: list[dict]) -> None:
        self.contracts = contracts
        self._exact_id: dict[str, set[str]] = {}
        self._exact_name: dict[str, set[str]] = {}
        self._stripped_id: dict[str, set[str]] = {}
        self._stripped_name: dict[str, set[str]] = {}
        for c in contracts:
            cid = c["customer_id"]
            for label, exact_idx, stripped_idx in (
                    (cid, self._exact_id, self._stripped_id),
                    (c.get("customer_name"), self._exact_name,
                     self._stripped_name)):
                exact = normalized_key(label or "")
                stripped = canonical_key(label or "")
                if exact:
                    exact_idx.setdefault(exact, set()).add(cid)
                if stripped:
                    stripped_idx.setdefault(stripped, set()).add(cid)

    def _indexes(self) -> list[dict[str, set[str]]]:
        # Most authoritative first: exact id, exact name, then the
        # suffix-stripped fallbacks.
        return [self._exact_id, self._exact_name,
                self._stripped_id, self._stripped_name]

    def resolve(self, label: str) -> str | None:
        exact = normalized_key(label or "")
        stripped = canonical_key(label or "")
        for key, index in ((exact, self._exact_id),
                           (exact, self._exact_name),
                           (stripped, self._stripped_id),
                           (stripped, self._stripped_name)):
            hits = index.get(key, set())
            if len(hits) == 1:
                return next(iter(hits))
        return None

    def explain(self, label: str) -> str:
        exact = normalized_key(label or "")
        stripped = canonical_key(label or "")
        for key, index in ((exact, self._exact_id),
                           (exact, self._exact_name),
                           (stripped, self._stripped_id),
                           (stripped, self._stripped_name)):
            hits = index.get(key, set())
            if len(hits) == 1:
                return f"matched '{label}' to {next(iter(hits))}"
            if len(hits) > 1:
                return (f"ambiguous: '{label}' matches "
                        f"{', '.join(sorted(hits))}")
        return "no contract matches"
