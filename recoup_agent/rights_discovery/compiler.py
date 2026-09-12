"""Deterministic RightSpec compiler — NO LLM imports, no eval.

A verified candidate is compiled only if its trigger and calculation validate
against closed allowlisted grammars and every constant is grounded verbatim
in the cited source quote. Anything else fails closed.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from ..reconciliation import CONFIDENCE_THRESHOLD  # noqa: F401 (re-exported for callers)
from ..rights_graph.ids import stable_id
from .models import (
    CALCULATION_PRIMITIVES, TRIGGER_OPERATORS, CandidateStatus,
    CompiledRight, CompileFailure, RightSpec,
)

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


def parse_constant_value(raw: Any, kind: str):
    """AI gives constants as written ('$15,000', '99.95%'); normalize to a
    float (percent -> decimal) or a date ISO string."""
    if isinstance(raw, (int, float)):
        return float(raw) / 100.0 if kind == "percentage" and raw > 1.5 else float(raw)
    s = str(raw or "").strip()
    if kind == "date":
        d = _parse_date_str(s)
        return d.isoformat() if d else None
    m = _NUM_RE.search(s)
    if not m:
        return None
    val = float(m.group(1).replace(",", ""))
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
    if isinstance(raw, (int, float)):
        return float(raw)
    m = _NUM_RE.search(str(raw or ""))
    return float(m.group(1).replace(",", "")) if m else None


def ground_constant(name: str, value: Any, kind: str, quote: str) -> bool:
    """A constant is grounded iff its value appears verbatim in the cited
    source quote (numeric equality, abs tol 1e-9; dates compare as dates)."""
    if kind == "date":
        target = _parse_date_str(str(value))
        if target is None:
            return False
        for m in _DATE_ISO_RE.findall(quote or ""):
            if _parse_date_str(m) == target:
                return True
        for m in _DATE_LONG_RE.findall(quote or ""):
            if _parse_date_str(m) == target:
                return True
        return False
    try:
        target = float(parse_constant_value(value, kind))
    except (TypeError, ValueError):
        return False
    return any(abs(n - target) < 1e-9 for n in numbers_in_text(quote or ""))


# ---- schema validation --------------------------------------------------------

def _is_const_ref(v: Any) -> bool:
    return isinstance(v, dict) and isinstance(v.get("constant"), str) \
        and bool(v["constant"].strip())


def _validate_trigger(node: Any, constant_names: set[str],
                      reasons: list[str], path: str = "trigger") -> None:
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
            _validate_trigger(child, constant_names, reasons, f"{path}.children[{i}]")
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
    elif ctype == "tiered":
        if not isinstance(calc.get("quantity_observation"), str) \
                or not calc["quantity_observation"].strip():
            reasons.append("tiered requires quantity_observation")
        tiers = calc.get("tiers")
        if not isinstance(tiers, list) or not tiers:
            reasons.append("tiered requires a non-empty tiers list")
        else:
            for i, t in enumerate(tiers):
                if not _is_const_ref(t.get("rate")):
                    reasons.append(f"tiered.tiers[{i}].rate must be a constant ref")
                elif t["rate"]["constant"] not in constant_names:
                    reasons.append(f"tiered.tiers[{i}].rate constant not declared")
                if t.get("up_to") is not None:
                    if not _is_const_ref(t["up_to"]):
                        reasons.append(f"tiered.tiers[{i}].up_to must be a "
                                       "constant ref")
                    elif t["up_to"]["constant"] not in constant_names:
                        reasons.append(f"tiered.tiers[{i}].up_to constant not declared")
        if calc.get("above") is not None and not _is_const_ref(calc["above"]):
            reasons.append("tiered.above must be a constant ref")


def compile_candidate_right(candidate, document_text: str = ""
                            ) -> CompiledRight | CompileFailure:
    """verified candidate + grounded constants -> CompiledRight; else a
    CompileFailure with status needs_review|unsupported|legacy_routed."""
    cid = candidate.candidate_id
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
        constants[name] = {
            "name": name,
            "value": _literal_constant_value(c.get("value"), c.get("kind") or "amount"),
            "kind": c.get("kind") or "amount",
            "percent_token": "%" in str(c.get("value") or ""),
            "currency": None,
            "evidence_ref": candidate.evidence_refs[0]
            if candidate.evidence_refs else None,
        }
    if not constants:
        reasons.append("no contractual constants declared")
    constant_names = set(constants)

    trigger = _normalize_trigger(candidate.trigger_spec)
    if trigger is None:
        reasons.append("no structured trigger")
    else:
        _validate_trigger(trigger, constant_names, reasons)

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
            for t in calc.get("tiers") or []:
                if isinstance(t, dict):
                    rate_refs.append(t.get("rate"))
        for ref in rate_refs:
            if _is_const_ref(ref):
                c = constants.get(ref["constant"])
                if c and c.get("percent_token") and isinstance(
                        c["value"], float) and c["value"] > 1.5:
                    c["value"] = c["value"] / 100.0

    # constant grounding against the quoted source text
    for name, c in constants.items():
        if c["value"] is None or not ground_constant(
                name, c["value"], c["kind"], candidate.source_quote):
            reasons.append(f"constant {name}={c['value']} not found in cited source")

    if not candidate.required_observations:
        reasons.append("required_observations empty")
    for dfld in (candidate.effective_from, candidate.effective_until):
        if dfld and _parse_date_str(str(dfld)) is None:
            reasons.append(f"date '{dfld}' not parseable")

    currency = "USD" if "$" in (candidate.source_quote or "") else None
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
