"""Deterministic RightSpec compiler — NO LLM imports, no eval.

A verified candidate is compiled only if its trigger and calculation validate
against closed allowlisted grammars and every constant is grounded verbatim
in the cited source quote. Anything else fails closed.
"""
from __future__ import annotations

import math
import re
from dataclasses import fields as _dc_fields
from datetime import date, datetime
from typing import Any

from ..reconciliation import CONFIDENCE_THRESHOLD  # noqa: F401 (re-exported for callers)
from ..rights_graph.ids import stable_id
from .models import (
    CALCULATION_PRIMITIVES, TRIGGER_OPERATORS, CandidateStatus,
    CompiledRight, CompileFailure, ContractualConstant, RightSpec,
)

# ---- spec size limits (D-10) ------------------------------------------------

MAX_CONSTANTS = 64
MAX_TIERS = 32
MAX_TRIGGER_DEPTH = 8
MAX_TRIGGER_NODES = 64
MAX_QUOTE_CHARS = 20_000
MAX_EVIDENCE_REFS = 32
MAX_REQUIRED_OBSERVATIONS = 32
MAX_NAME_CHARS = 128
MAX_OPERANDS = 4

LEGACY_FAMILIES = {
    "committed_minimum", "usage_overage", "discount_expiration",
    "annual_escalator", "committed_seat_charge",
}

_FAMILY_ALIASES = {
    "minimum_commitment": "committed_minimum",
    "monthly_minimum": "committed_minimum",
    "minimum_monthly_fee": "committed_minimum",
    "overage": "usage_overage",
    "overage_rate": "usage_overage",
    "usage_overage": "usage_overage",
    "discount": "discount_expiration",
    "promotional_discount": "discount_expiration",
    "escalator": "annual_escalator",
    "price_escalator": "annual_escalator",
    "price_increase": "annual_escalator",
    "seats": "committed_seat_charge",
    "committed_seats": "committed_seat_charge",
    "seat_price": "committed_seat_charge",
    "seat_charge": "committed_seat_charge",
}

_NAME_KEYWORDS = ("minimum", "overage", "discount", "escalat", "seat")


def _norm_family(family: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (family or "").lower()).strip("_")


def route_family(right_family: str, right_name: str) -> str:
    """Legacy families never compile into the generic runtime — they are
    evaluated by recoup_agent.reconciliation."""
    fam = _norm_family(right_family)
    if fam in LEGACY_FAMILIES or fam in _FAMILY_ALIASES:
        return "legacy"
    name = (right_name or "").lower()
    if any(k in name for k in _NAME_KEYWORDS):
        return "legacy"
    return "generic"


# ---- constant grounding -----------------------------------------------------

_NUM_RE = re.compile(r"\$?\s*(\d[\d,]*(?:\.\d+)?)\s*%?")
_DATE_ISO_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_MONTHS = ("january|february|march|april|may|june|july|august|september|"
           "october|november|december")
_DATE_LONG_RE = re.compile(
    rf"\b({_MONTHS})\s+\d{{1,2}},?\s+\d{{4}}\b", re.IGNORECASE)


def numbers_in_text(text: str) -> list[float]:
    """All numeric tokens in text; a `N%` token yields both N and N/100."""
    out = []
    for m in _NUM_RE.finditer(text or ""):
        token = m.group(0)
        num = float(m.group(1).replace(",", ""))
        out.append(num)
        if "%" in token:
            out.append(num / 100.0)
    return out


def _parse_date_str(value: str) -> date | None:
    value = (value or "").strip()
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        pass
    try:
        return datetime.strptime(value, "%B %d, %Y").date()
    except ValueError:
        return None


_NUMERIC_LITERAL_RE = re.compile(
    r"^(-)?\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,6})?%?$")


def parse_numeric_literal(raw: Any, *, allow_negative: bool) -> float | None:
    """Strict ASCII numeric grammar. Returns the float value or None.

    Valid: '$15,000', '99.95%', '-250' (only when allow_negative), 1500,
    0.05. Rejected: expressions, code, non-ASCII digits, underscores,
    hex, exponents, NaN/inf, > 1e12 magnitudes."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
        if not math.isfinite(v) or abs(v) > 1e12:
            return None
        if v < 0 and not allow_negative:
            return None
        return v
    s = str(raw or "").strip()
    if not s or not s.isascii() or len(s) > 32:
        return None
    m = _NUMERIC_LITERAL_RE.match(s)
    if not m:
        return None
    negative = bool(m.group(1))
    if negative and not allow_negative:
        return None
    token = s.lstrip("-").lstrip("$").rstrip("%").replace(",", "")
    try:
        v = float(token)
    except ValueError:
        return None
    if negative:
        v = -v
    if not math.isfinite(v) or abs(v) > 1e12:
        return None
    return v


def parse_constant_value(raw: Any, kind: str):
    """AI gives constants as written ('$15,000', '99.95%'); normalize to a
    float (percent -> decimal) or a date ISO string."""
    if kind == "date":
        d = _parse_date_str(str(raw or "").strip())
        return d.isoformat() if d else None
    val = parse_numeric_literal(raw, allow_negative=kind in ("amount", "threshold"))
    if val is None:
        return None
    # Percentages-as-rates normalize to decimals; thresholds stay in the units
    # the observation is reported in (e.g. uptime 99.95).
    if kind == "percentage" and val > 1.5:
        val = val / 100.0
    return val


_OP_ALIASES = {
    "less_than": "lt", "less_than_or_equal": "lte", "less_than_or_equal_to": "lte",
    "greater_than": "gt", "greater_than_or_equal": "gte",
    "greater_than_or_equal_to": "gte", "equals": "eq", "equal_to": "eq",
    "equal": "eq", "not_equal": "neq", "not_equal_to": "neq",
    "below": "lt", "above": "gt", "at_or_below": "lte", "at_or_above": "gte",
}


def _normalize_trigger(node: Any) -> Any:
    """Translate common model-emitted trigger shapes ({'operator': ...,
    'left_operand': ..., 'right_operand': ...}) into the TriggerSpec grammar;
    leaves anything already in shape (or unrecognizable) untouched so
    validation stays the authority."""
    if not isinstance(node, dict):
        return node
    node = dict(node)
    if node.get("children"):
        node["children"] = [_normalize_trigger(c) for c in node["children"]]
    if "op" in node:
        return node
    operator = str(node.get("operator") or node.get("type") or "").lower()
    op = _OP_ALIASES.get(operator, operator or None)
    if op in ("and", "or"):
        return {"op": op, "children": [_normalize_trigger(c)
                                       for c in node.get("children") or []]}
    left = node.get("left_operand") if isinstance(node.get("left_operand"), dict) else {}
    right = node.get("right_operand") if isinstance(node.get("right_operand"), dict) else {}
    obs = node.get("observation") or left.get("observation")
    out = {"op": op, "observation": obs}
    if "constant" in right:
        out["value"] = right
    elif right:
        out["value"] = right  # validation decides (inline literals fail closed)
    elif node.get("value") is not None:
        out["value"] = node["value"]
    return out


def _literal_constant_value(raw: Any, kind: str):
    """The constant's value as written: '5%' -> 5.0, '$15,000' -> 15000,
    dates -> ISO. Rate-vs-threshold normalization happens later, at the
    position where the constant is referenced."""
    if kind == "date":
        return parse_constant_value(raw, "date")
    return parse_numeric_literal(raw, allow_negative=kind in ("amount", "threshold"))


def ground_constant(name: str, value: Any, kind: str, quote: str,
                    quote_numbers: list[float] | None = None) -> bool:
    """A constant is grounded iff its value appears verbatim in the cited
    source quote (numeric equality, abs tol 1e-9; dates compare as dates)."""
    quote = (quote or "")[:MAX_QUOTE_CHARS]
    if kind == "date":
        target = _parse_date_str(str(value))
        if target is None:
            return False
        for m in _DATE_ISO_RE.findall(quote):
            if _parse_date_str(m) == target:
                return True
        for m in _DATE_LONG_RE.findall(quote):
            if _parse_date_str(m) == target:
                return True
        return False
    try:
        target = float(parse_constant_value(value, kind))
    except (TypeError, ValueError):
        return False
    if quote_numbers is None:
        quote_numbers = numbers_in_text(quote)
    return any(abs(n - target) < 1e-9 for n in quote_numbers)


# ---- schema validation --------------------------------------------------------

def _is_const_ref(v: Any) -> bool:
    return isinstance(v, dict) and isinstance(v.get("constant"), str) \
        and bool(v["constant"].strip())


_OBSERVED_DATE_OPS = {
    "observed_date_before", "observed_date_on_or_before",
    "observed_date_after", "observed_date_on_or_after",
}


def _validate_trigger(node: Any, constant_names: set[str],
                      reasons: list[str], path: str = "trigger",
                      constant_kinds: dict[str, str] | None = None) -> None:
    if not isinstance(node, dict) or not isinstance(node.get("op"), str):
        reasons.append(f"{path}: not an object with an 'op'")
        return
    op = node["op"]
    if op not in TRIGGER_OPERATORS:
        reasons.append(f"{path}: operator '{op}' not allowed")
        return
    if op in ("and", "or"):
        children = node.get("children")
        if not isinstance(children, list) or not children:
            reasons.append(f"{path}: '{op}' requires non-empty children")
            return
        for i, child in enumerate(children):
            _validate_trigger(child, constant_names, reasons,
                              f"{path}.children[{i}]", constant_kinds)
        return
    if op == "event_exists":
        obs = node.get("observation")
        if not isinstance(obs, str) or not obs.strip():
            reasons.append(f"{path}: event_exists requires an observation name")
        return
    obs = node.get("observation")
    if not isinstance(obs, str) or not obs.strip():
        reasons.append(f"{path}: '{op}' requires an observation name")
    if op == "between":
        for key in ("low", "high"):
            ref = node.get(key)
            if not _is_const_ref(ref):
                reasons.append(f"{path}.between.{key}: must be a constant ref")
            elif ref["constant"] not in constant_names:
                reasons.append(f"{path}.between.{key}: constant "
                               f"'{ref['constant']}' is not declared")
        return
    ref = node.get("value")
    if not _is_const_ref(ref):
        reasons.append(f"{path}.{op}: value must be {{'constant': name}}")
    elif ref["constant"] not in constant_names:
        reasons.append(f"{path}.{op}: constant '{ref['constant']}' is not declared")
    elif op in _OBSERVED_DATE_OPS and constant_kinds is not None \
            and constant_kinds.get(ref["constant"]) != "date":
        reasons.append(f"{path}.{op}: constant '{ref['constant']}' must be "
                       "of kind 'date'")


def _operand_ref(v: Any, constant_names: set[str], reasons: list[str], path: str) -> None:
    if _is_const_ref(v):
        if v["constant"] not in constant_names:
            reasons.append(f"{path}: constant '{v['constant']}' is not declared")
        return
    if isinstance(v, dict) and isinstance(v.get("observation"), str) \
            and v["observation"].strip():
        return
    reasons.append(f"{path}: operand must be a constant or observation ref")


def _validate_calculation(calc: Any, constant_names: set[str],
                          reasons: list[str]) -> None:
    if not isinstance(calc, dict):
        reasons.append("calculation: not an object")
        return
    ctype = calc.get("type")
    if ctype in ("none", "unsupported", None):
        reasons.append("calculation: unsupported")
        return
    if ctype not in CALCULATION_PRIMITIVES:
        reasons.append(f"calculation: type '{ctype}' not allowed")
        return
    if ctype == "fixed_amount":
        if not _is_const_ref(calc.get("amount")):
            reasons.append("fixed_amount.amount must be a constant ref")
        elif calc["amount"]["constant"] not in constant_names:
            reasons.append("fixed_amount.amount constant not declared")
    elif ctype == "percentage_of":
        if not _is_const_ref(calc.get("rate")):
            reasons.append("percentage_of.rate must be a constant ref")
        elif calc["rate"]["constant"] not in constant_names:
            reasons.append("percentage_of.rate constant not declared")
        if not isinstance(calc.get("base_observation"), str) \
                or not calc["base_observation"].strip():
            reasons.append("percentage_of requires base_observation")
    elif ctype == "per_unit":
        if not _is_const_ref(calc.get("rate")):
            reasons.append("per_unit.rate must be a constant ref")
        elif calc["rate"]["constant"] not in constant_names:
            reasons.append("per_unit.rate constant not declared")
        if not isinstance(calc.get("quantity_observation"), str) \
                or not calc["quantity_observation"].strip():
            reasons.append("per_unit requires quantity_observation")
        if calc.get("above") is not None:
            if not _is_const_ref(calc["above"]):
                reasons.append("per_unit.above must be a constant ref")
            elif calc["above"]["constant"] not in constant_names:
                reasons.append("per_unit.above constant not declared")
    elif ctype == "difference":
        for key in ("minuend", "subtrahend"):
            _operand_ref(calc.get(key), constant_names, reasons,
                         f"difference.{key}")
    elif ctype in ("tiered", "volume_tiered"):
        if not isinstance(calc.get("quantity_observation"), str) \
                or not calc["quantity_observation"].strip():
            reasons.append(f"{ctype} requires quantity_observation")
        tiers = calc.get("tiers")
        if not isinstance(tiers, list) or not tiers:
            reasons.append(f"{ctype} requires a non-empty tiers list")
        elif len(tiers) > MAX_TIERS:
            reasons.append(f"{ctype}.tiers exceeds MAX_TIERS ({MAX_TIERS})")
        else:
            for i, t in enumerate(tiers):
                if not _is_const_ref(t.get("rate")):
                    reasons.append(f"{ctype}.tiers[{i}].rate must be a constant ref")
                elif t["rate"]["constant"] not in constant_names:
                    reasons.append(f"{ctype}.tiers[{i}].rate constant not declared")
                if t.get("up_to") is not None:
                    if not _is_const_ref(t["up_to"]):
                        reasons.append(f"{ctype}.tiers[{i}].up_to must be a "
                                       "constant ref")
                    elif t["up_to"]["constant"] not in constant_names:
                        reasons.append(f"{ctype}.tiers[{i}].up_to constant not declared")
        if calc.get("above") is not None and not _is_const_ref(calc["above"]):
            reasons.append(f"{ctype}.above must be a constant ref")
    elif ctype in ("min_of", "max_of"):
        operands = calc.get("operands")
        if not isinstance(operands, list) or len(operands) < 2:
            reasons.append(f"{ctype} requires >= 2 operands")
        elif len(operands) > MAX_OPERANDS:
            reasons.append(f"{ctype}.operands exceeds MAX_OPERANDS "
                           f"({MAX_OPERANDS})")
        else:
            for i, ref in enumerate(operands):
                _operand_ref(ref, constant_names, reasons,
                             f"{ctype}.operands[{i}]")
    elif ctype == "banded_percentage_of":
        if not isinstance(calc.get("base_observation"), str) \
                or not calc["base_observation"].strip():
            reasons.append("banded_percentage_of requires base_observation")
        if not isinstance(calc.get("band_observation"), str) \
                or not calc["band_observation"].strip():
            reasons.append("banded_percentage_of requires band_observation")
        bands = calc.get("bands")
        if not isinstance(bands, list) or not bands:
            reasons.append("banded_percentage_of requires a non-empty bands list")
        elif len(bands) > MAX_TIERS:
            reasons.append(f"banded_percentage_of.bands exceeds MAX_TIERS "
                           f"({MAX_TIERS})")
        else:
            for i, b in enumerate(bands):
                if not _is_const_ref(b.get("rate")):
                    reasons.append(f"bands[{i}].rate must be a constant ref")
                elif b["rate"]["constant"] not in constant_names:
                    reasons.append(f"bands[{i}].rate constant not declared")
                if b.get("up_to") is not None:
                    if not _is_const_ref(b["up_to"]):
                        reasons.append(f"bands[{i}].up_to must be a constant ref")
                    elif b["up_to"]["constant"] not in constant_names:
                        reasons.append(f"bands[{i}].up_to constant not declared")

    # cap/floor modifiers are allowed on every primitive; constant refs only.
    for modifier in ("cap", "floor"):
        ref = calc.get(modifier)
        if ref is not None:
            if not _is_const_ref(ref):
                reasons.append(f"{ctype}.{modifier} must be a constant ref")
            elif ref["constant"] not in constant_names:
                reasons.append(f"{ctype}.{modifier} constant not declared")


# ---- closed-grammar key sets (R-06) ------------------------------------------
# Only the keys the runtime actually reads are allowed anywhere in a spec.
_RIGHT_SPEC_FIELDS = {f.name for f in _dc_fields(RightSpec)}
_CONSTANT_FIELDS = {f.name for f in _dc_fields(ContractualConstant)}
_TRIGGER_NODE_KEYS = {"op", "observation", "value", "low", "high", "children"}
_CONST_REF_KEYS = {"constant"}
_OPERAND_REF_KEYS = {"constant", "observation"}
_CALC_NODE_KEYS = {
    "type", "amount", "rate", "base_observation", "quantity_observation",
    "above", "minuend", "subtrahend", "floor_zero", "tiers", "bands",
    "band_observation", "cap", "floor", "operands",
}
_BAND_KEYS = {"up_to", "rate"}


def _unknown_keys(node: Any, allowed: set[str], path: str,
                  reasons: list[str]) -> None:
    if isinstance(node, dict):
        bad = sorted(set(node) - allowed)
        if bad:
            reasons.append(f"unknown_spec_keys: {path}: {bad}")


def _spec_key_reasons(candidate) -> list[str]:
    """Reject any key the runtime never reads — anywhere in the spec."""
    reasons: list[str] = []
    if isinstance(candidate, dict):
        _unknown_keys(candidate, _RIGHT_SPEC_FIELDS, "spec", reasons)
        constants = candidate.get("contractual_constants") or []
        trigger = candidate.get("trigger")
        calc = candidate.get("calculation")
    else:
        constants = (candidate.metadata or {}).get("constants") or []
        trigger = candidate.trigger_spec
        calc = candidate.calculation_spec

    if isinstance(candidate, dict):
        reasons.append("candidate is not a CandidateFinancialRight")

    for i, c in enumerate(constants):
        _unknown_keys(c, _CONSTANT_FIELDS, f"constants[{i}]", reasons)

    stack = [(trigger, "trigger")]
    while stack:
        node, path = stack.pop()
        if not isinstance(node, dict):
            continue
        _unknown_keys(node, _TRIGGER_NODE_KEYS, path, reasons)
        for key in ("value", "low", "high"):
            _unknown_keys(node.get(key), _CONST_REF_KEYS,
                          f"{path}.{key}", reasons)
        for i, child in enumerate(node.get("children") or []):
            stack.append((child, f"{path}.children[{i}]"))

    if isinstance(calc, dict):
        _unknown_keys(calc, _CALC_NODE_KEYS, "calculation", reasons)
        for key in ("amount", "rate", "above", "minuend", "subtrahend",
                    "cap", "floor"):
            _unknown_keys(calc.get(key), _CONST_REF_KEYS,
                          f"calculation.{key}", reasons)
        for seq_key in ("tiers", "bands"):
            for i, b in enumerate(calc.get(seq_key) or []):
                _unknown_keys(b, _BAND_KEYS,
                              f"calculation.{seq_key}[{i}]", reasons)
                if isinstance(b, dict):
                    for key in ("up_to", "rate"):
                        _unknown_keys(b.get(key), _CONST_REF_KEYS,
                                      f"calculation.{seq_key}[{i}].{key}",
                                      reasons)
        for i, op in enumerate(calc.get("operands") or []):
            _unknown_keys(op, _OPERAND_REF_KEYS,
                          f"calculation.operands[{i}]", reasons)
    return reasons


def _check_limits(candidate) -> list[str]:
    """Bounded scan of candidate size limits; returns violation reasons.
    Runs FIRST in compile_candidate_right — nothing else executes when a
    spec exceeds limits."""
    reasons: list[str] = _spec_key_reasons(candidate)
    if isinstance(candidate, dict):
        return reasons
    raw_constants = (candidate.metadata or {}).get("constants") or []
    if len(raw_constants) > MAX_CONSTANTS:
        reasons.append(f"constants: {len(raw_constants)} > MAX_CONSTANTS "
                       f"({MAX_CONSTANTS})")
    for c in raw_constants:
        if len(str(c.get("name") or "")) > MAX_NAME_CHARS:
            reasons.append("constant name exceeds MAX_NAME_CHARS")
            break
    if len(candidate.source_quote or "") > MAX_QUOTE_CHARS:
        reasons.append(f"source_quote exceeds MAX_QUOTE_CHARS "
                       f"({MAX_QUOTE_CHARS})")
    if len(candidate.evidence_refs or []) > MAX_EVIDENCE_REFS:
        reasons.append(f"evidence_refs exceeds MAX_EVIDENCE_REFS "
                       f"({MAX_EVIDENCE_REFS})")
    if len(candidate.required_observations or []) > MAX_REQUIRED_OBSERVATIONS:
        reasons.append("required_observations exceeds "
                       f"MAX_REQUIRED_OBSERVATIONS ({MAX_REQUIRED_OBSERVATIONS})")
    for obs in candidate.required_observations or []:
        if len(str(obs)) > MAX_NAME_CHARS:
            reasons.append("observation name exceeds MAX_NAME_CHARS")
            break
    if len(candidate.right_name or "") > MAX_NAME_CHARS:
        reasons.append("right_name exceeds MAX_NAME_CHARS")

    # Trigger: one bounded walk stops at the first depth/node violation.
    nodes = 0
    stack = [(candidate.trigger_spec, 1)]
    while stack:
        node, depth = stack.pop()
        if not isinstance(node, dict):
            continue
        nodes += 1
        if nodes > MAX_TRIGGER_NODES or depth > MAX_TRIGGER_DEPTH:
            reasons.append("trigger exceeds MAX_TRIGGER_DEPTH "
                           f"({MAX_TRIGGER_DEPTH}) or MAX_TRIGGER_NODES "
                           f"({MAX_TRIGGER_NODES})")
            break
        for child in node.get("children") or []:
            if isinstance(child, dict):
                stack.append((child, depth + 1))

    calc = candidate.calculation_spec
    if isinstance(calc, dict):
        for key in ("tiers", "bands"):
            seq = calc.get(key)
            if isinstance(seq, list) and len(seq) > MAX_TIERS:
                reasons.append(f"{key}: {len(seq)} > MAX_TIERS ({MAX_TIERS})")
        ops = calc.get("operands")
        if isinstance(ops, list) and len(ops) > MAX_OPERANDS:
            reasons.append(f"operands: {len(ops)} > MAX_OPERANDS ({MAX_OPERANDS})")
    return reasons


def compile_candidate_right(candidate, document_text: str = ""
                            ) -> CompiledRight | CompileFailure:
    """verified candidate + grounded constants -> CompiledRight; else a
    CompileFailure with status needs_review|unsupported|legacy_routed."""
    cid = candidate.get("candidate_id") if isinstance(candidate, dict) \
        else candidate.candidate_id
    limit_reasons = _check_limits(candidate)
    if limit_reasons:
        return CompileFailure(candidate_id=cid,
                              status=CandidateStatus.rejected.value,
                              reasons=limit_reasons)
    if candidate.status != CandidateStatus.verified.value:
        return CompileFailure(candidate_id=cid,
                              status=CandidateStatus.needs_review.value,
                              reasons=["candidate not verified"])
    if route_family(candidate.right_family, candidate.right_name) == "legacy":
        return CompileFailure(
            candidate_id=cid, status=CandidateStatus.legacy_routed.value,
            reasons=["evaluated by recoup_agent.reconciliation; not compiled"])

    reasons: list[str] = []
    # constants: name -> normalized value (discovery stores them in metadata)
    constants: dict[str, dict] = {}
    raw_constants = (candidate.metadata or {}).get("constants") or []
    for c in raw_constants:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        kind = c.get("kind") or "amount"
        value = _literal_constant_value(c.get("value"), kind)
        if value is None and kind != "date":
            reasons.append(f"constant {name}: value '{c.get('value')}' is not "
                           "a valid numeric literal")
        constants[name] = {
            "name": name,
            "value": value,
            "kind": kind,
            "percent_token": "%" in str(c.get("value") or ""),
            "currency": None,
            "evidence_ref": candidate.evidence_refs[0]
            if candidate.evidence_refs else None,
        }
    if not constants:
        reasons.append("no contractual constants declared")
    constant_names = set(constants)
    constant_kinds = {n: c["kind"] for n, c in constants.items()}

    trigger = _normalize_trigger(candidate.trigger_spec)
    if trigger is None:
        reasons.append("no structured trigger")
    else:
        _validate_trigger(trigger, constant_names, reasons,
                          constant_kinds=constant_kinds)

    calc = candidate.calculation_spec
    if calc is None:
        reasons.append("no structured calculation")
    else:
        _validate_calculation(calc, constant_names, reasons)
        # Rates normalize percent tokens to decimals ("5%" -> 0.05); trigger
        # thresholds keep the literal value because observations are reported
        # in document units.
        rate_refs = []
        if isinstance(calc, dict):
            rate_refs.append(calc.get("rate"))
            for seq_key in ("tiers", "bands"):
                for t in calc.get(seq_key) or []:
                    if isinstance(t, dict):
                        rate_refs.append(t.get("rate"))
        for ref in rate_refs:
            if _is_const_ref(ref):
                c = constants.get(ref["constant"])
                if c and c.get("percent_token") and isinstance(
                        c["value"], float) and c["value"] > 1.5:
                    c["value"] = c["value"] / 100.0

    # constant grounding against the quoted source text (scanned once)
    capped_quote = (candidate.source_quote or "")[:MAX_QUOTE_CHARS]
    quote_numbers = numbers_in_text(capped_quote)
    for name, c in constants.items():
        if c["value"] is None or not ground_constant(
                name, c["value"], c["kind"], candidate.source_quote,
                quote_numbers=quote_numbers):
            reasons.append(f"constant {name}={c['value']} not found in cited source")

    if not candidate.required_observations:
        reasons.append("required_observations empty")
    for dfld in (candidate.effective_from, candidate.effective_until):
        if dfld and _parse_date_str(str(dfld)) is None:
            reasons.append(f"date '{dfld}' not parseable")

    currency = "USD" if "$" in (candidate.source_quote or "") else None
    if re.search(r"€|£|\b(?:EUR|GBP|CAD|AUD|JPY)\b", candidate.source_quote or "", re.IGNORECASE):
        reasons.append("unsupported currency in source quote")
    # Only monetary constants need a grounded currency; rights whose constants
    # are all non-monetary (percentages, quantities, thresholds) stay
    # currency-neutral and record USD as the reporting unit.
    needs_currency = bool(constants) and any(
        c["kind"] in ("amount", "rate") for c in constants.values())
    if currency is None:
        if needs_currency:
            reasons.append("currency could not be grounded in source quote")
        else:
            currency = "USD"

    if reasons:
        status = (CandidateStatus.unsupported.value
                  if any("unsupported" in r or "not allowed" in r or
                         "no structured" in r for r in reasons)
                  else CandidateStatus.needs_review.value)
        return CompileFailure(candidate_id=cid, status=status, reasons=reasons)

    spec = RightSpec(
        spec_version="1.0",
        right_id=stable_id("right", candidate.account_id, candidate.source_id,
                           candidate.right_family, candidate.source_quote),
        right_family=candidate.right_family,
        holder_party_id=candidate.holder_party_id,
        obligor_party_id=candidate.obligor_party_id,
        trigger=trigger,
        calculation=calc,
        required_observations=list(candidate.required_observations),
        contractual_constants=list(constants.values()),
        currency=currency,
        effective_from=candidate.effective_from,
        effective_until=candidate.effective_until,
        evidence_refs=list(candidate.evidence_refs) or
        [stable_id("ev", candidate.source_id, candidate.source_quote)],
        actual_observation=(candidate.metadata or {}).get("actual_observation"),
        actual_observation_optional=bool(
            (candidate.metadata or {}).get("actual_observation_optional")),
    )
    candidate.status = CandidateStatus.compiled.value
    return CompiledRight(
        spec=spec.to_dict(),
        candidate_id=cid,
        compiled_at=datetime.utcnow().isoformat() + "Z",
        discovery_model=candidate.discovery_model,
        verification_model=candidate.verification_model,
    )
