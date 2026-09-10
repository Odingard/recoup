"""Customer identity resolution across contract documents and billing/usage
exports, which rarely spell a customer's name the same way."""
from __future__ import annotations

import re

SUFFIXES = {"llc", "inc", "co", "corp", "corporation", "company", "group", "grp",
            "ltd", "limited", "plc", "gmbh", "the", "and", "&"}
ABBREVIATIONS = {"intl": "international", "svcs": "services", "mfg": "manufacturing",
                 "tech": "technology", "assoc": "associates", "bros": "brothers"}


def _tokens(name: str) -> list[str]:
    return [ABBREVIATIONS.get(t, t) for t in re.sub(r"[^a-z0-9]+", " ", name.lower()).split()]


def canonical_key(name: str) -> str:
    """Lowercase, punctuation-insensitive key with suffixes/abbreviations normalized."""
    kept = [t for t in _tokens(name or "") if t not in SUFFIXES]
    return "_".join(kept)


class CustomerResolver:
    """Maps a billing/usage label to a contract customer_id."""

    def __init__(self, contracts: list[dict]) -> None:
        self.contracts = contracts
        self._by_key: dict[str, str] = {}
        self._token_sets: dict[str, tuple[str, ...]] = {}
        for c in contracts:
            cid = c["customer_id"]
            for label in {c.get("customer_name"), cid}:
                key = canonical_key(label or "")
                if key:
                    self._by_key.setdefault(key, cid)
                    self._token_sets.setdefault(key, tuple(key.split("_")))

    def _prefix_matches(self, label_tokens: tuple[str, ...]) -> set[str]:
        hits = set()
        for key, tokens in self._token_sets.items():
            if tokens[: len(label_tokens)] == label_tokens or label_tokens[: len(tokens)] == tokens:
                hits.add(self._by_key[key])
        return hits

    def _first_token_matches(self, first: str) -> set[str]:
        return {self._by_key[key] for key, tokens in self._token_sets.items()
                if tokens and tokens[0] == first}

    def resolve(self, label: str) -> str | None:
        key = canonical_key(label or "")
        if not key:
            return None
        if key in self._by_key:
            return self._by_key[key]
        tokens = tuple(key.split("_"))
        hits = self._prefix_matches(tokens)
        if len(hits) == 1:
            return hits.pop()
        if len(hits) > 1:
            return None
        first_hits = self._first_token_matches(tokens[0]) if tokens else set()
        if len(first_hits) == 1:
            return first_hits.pop()
        return None

    def explain(self, label: str) -> str:
        key = canonical_key(label or "")
        if not key:
            return f"no contract matches '{label}' (unparsable name)"
        if key in self._by_key:
            return f"matched '{label}' to {self._by_key[key]}"
        tokens = tuple(key.split("_"))
        hits = self._prefix_matches(tokens) | (self._first_token_matches(tokens[0]) if tokens else set())
        if len(hits) > 1:
            return f"ambiguous: '{label}' matches {', '.join(sorted(hits))}"
        return f"no contract matches '{label}' (normalized '{key}')"
