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


def _verbatim_key(label: str) -> str:
    """Case-insensitive, whitespace-collapsed label; punctuation preserved."""
    return " ".join(str(label or "").strip().casefold().split())


class CustomerResolver:
    """Maps a billing/usage label to a contract customer_id."""

    def __init__(self, contracts: list[dict]) -> None:
        self.contracts = contracts
        self._verbatim_id: dict[str, set[str]] = {}
        self._verbatim_name: dict[str, set[str]] = {}
        self._exact_id: dict[str, set[str]] = {}
        self._exact_name: dict[str, set[str]] = {}
        self._stripped_id: dict[str, set[str]] = {}
        self._stripped_name: dict[str, set[str]] = {}
        for c in contracts:
            cid = c["customer_id"]
            for label, indexes in (
                    (cid, (self._verbatim_id, self._exact_id,
                           self._stripped_id)),
                    (c.get("customer_name"),
                     (self._verbatim_name, self._exact_name,
                      self._stripped_name))):
                verbatim_idx, exact_idx, stripped_idx = indexes
                verbatim = _verbatim_key(label)
                exact = normalized_key(label or "")
                stripped = canonical_key(label or "")
                if verbatim:
                    verbatim_idx.setdefault(verbatim, set()).add(cid)
                if exact:
                    exact_idx.setdefault(exact, set()).add(cid)
                if stripped:
                    stripped_idx.setdefault(stripped, set()).add(cid)

    def _lookup(self, label: str) -> list[set[str]]:
        """Hit sets in authority order: verbatim id/name, exact id/name,
        suffix-stripped id/name."""
        verbatim = _verbatim_key(label)
        exact = normalized_key(label or "")
        stripped = canonical_key(label or "")
        return [index.get(key, set()) for key, index in (
            (verbatim, self._verbatim_id),
            (verbatim, self._verbatim_name),
            (exact, self._exact_id),
            (exact, self._exact_name),
            (stripped, self._stripped_id),
            (stripped, self._stripped_name))]

    def resolve(self, label: str) -> str | None:
        for hits in self._lookup(label):
            if len(hits) == 1:
                return next(iter(hits))
        return None

    def explain(self, label: str) -> str:
        first_ambiguous: set[str] | None = None
        for hits in self._lookup(label):
            if len(hits) == 1:
                return f"matched '{label}' to {next(iter(hits))}"
            if len(hits) > 1 and first_ambiguous is None:
                first_ambiguous = hits
        if first_ambiguous is not None:
            return (f"ambiguous: '{label}' matches "
                    f"{', '.join(sorted(first_ambiguous))}")
        return "no contract matches"
