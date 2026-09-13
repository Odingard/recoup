"""Deterministic RightSpec runtime — NO LLM imports, no eval.

Evaluates a compiled right's allowlisted trigger grammar against observed
facts and applies its calculation primitive. Floats rounded to 2dp, same as
recoup_agent.reconciliation.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .models import EvaluationResult, RightSpec
from ..money import quantize, normalize_currency, is_supported


def _parse_date(value) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _period_end(period: str) -> date | None:
    start = _parse_date(period + "-01")
    if start is None:
        return None
    if start.month == 12:
        return date(start.year, 12, 31)
    return date(start.year, start.month + 1, 1) - timedelta(days=1)


class _Ctx:
    """Evaluation context: constants, and the period's observations by type."""

    def __init__(self, spec: RightSpec, observations: list[dict], period: str):
        self.currency_mismatch = False
        self.negative_quantity = False
        self.constants = {c["name"]: c["value"]
                          for c in spec.contractual_constants or []}
        self.by_type: dict[str, dict] = {}
        quantity_names: set[str] = set()
        pending = [spec.calculation.model_dump() if hasattr(spec.calculation, "model_dump")
                   else spec.calculation]
        while pending:
            node = pending.pop()
            if not isinstance(node, dict):
                continue
            for key, value in node.items():
                if key == "quantity_observation" and isinstance(value, str):
                    quantity_names.add(value)
                elif isinstance(value, dict):
                    pending.append(value)
                elif isinstance(value, list):
                    pending.extend(value)
        for o in observations:
            if str(o.get("period", "")).startswith(period):
                if o.get("currency") and (not is_supported(o.get("currency"))
                        or normalize_currency(o.get("currency")) != normalize_currency(spec.currency)):
                    self.currency_mismatch = True
                name = o.get("type") or o.get("observation_type")
                if name in quantity_names and (o.get("value") or 0) < 0:
                    self.negative_quantity = True
                self.by_type.setdefault(name, o)
        self.period = period
        self.trace: dict = {}

    def const(self, ref) -> float | None:
        if isinstance(ref, dict) and "constant" in ref:
            v = self.constants.get(ref["constant"])
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        return None

    def const_or_date(self, ref):
        if isinstance(ref, dict) and "constant" in ref:
            return self.constants.get(ref["constant"])
        return None

    def obs_amount(self, obs_type: str) -> float | None:
        o = self.by_type.get(obs_type)
        if o is None:
            return None
        for key in ("amount", "value", "quantity"):
            if o.get(key) is not None:
                return float(o[key])
        return None

    def obs_quantity(self, obs_type: str) -> float | None:
        o = self.by_type.get(obs_type)
        if o is None:
            return None
        for key in ("quantity", "value", "amount"):
            if o.get(key) is not None:
                return float(o[key])
        return None


def _eval_trigger(node: dict, ctx: _Ctx) -> bool | None:
    """-> True/False, or None when a required observation is missing."""
    op = node["op"]
    if op == "and":
        results = [_eval_trigger(c, ctx) for c in node["children"]]
        if all(r is True for r in results):
            return True
        if any(r is False for r in results):
            return False
        return None
    if op == "or":
        results = [_eval_trigger(c, ctx) for c in node["children"]]
        if any(r is True for r in results):
            return True
        if all(r is False for r in results):
            return False
        return None
    if op == "event_exists":
        return node["observation"] in ctx.by_type

    observed = ctx.obs_amount(node["observation"])
    if observed is None:
        observed = ctx.obs_quantity(node["observation"])
    if observed is None:
        ctx.trace[node["observation"]] = "missing observation"
        return None

    if op in ("date_reached", "date_before"):
        target = _parse_date(ctx.const_or_date(node.get("value")))
        end = _period_end(ctx.period)
        if target is None or end is None:
            return None
        result = end >= target if op == "date_reached" else end < target
    elif op == "between":
        low = ctx.const(node.get("low"))
        high = ctx.const(node.get("high"))
        if low is None or high is None:
            return None
        result = low <= observed <= high
    else:
        threshold = ctx.const(node.get("value"))
        if threshold is None:
            return None
        result = {
            "eq": observed == threshold,
            "neq": observed != threshold,
            "gt": observed > threshold,
            "gte": observed >= threshold,
            "lt": observed < threshold,
            "lte": observed <= threshold,
        }[op]
    ctx.trace[node["observation"]] = {
        "observed": observed, "op": op,
        "threshold": node.get("value"), "result": result,
    }
    return result


def _operand(ref, ctx: _Ctx) -> float | None:
    if not isinstance(ref, dict):
        return None
    if "constant" in ref:
        return ctx.const(ref)
    if "observation" in ref:
        v = ctx.obs_amount(ref["observation"])
        return v if v is not None else ctx.obs_quantity(ref["observation"])
    return None


def _calc_amount(calc: dict, ctx: _Ctx) -> float | None:
    ctype = calc["type"]
    if ctype == "fixed_amount":
        return ctx.const(calc["amount"])
    if ctype == "percentage_of":
        rate = ctx.const(calc["rate"])
        base = ctx.obs_amount(calc["base_observation"])
        if rate is None or base is None:
            return None
        return rate * base
    if ctype == "per_unit":
        rate = ctx.const(calc["rate"])
        qty = ctx.obs_quantity(calc["quantity_observation"])
        if rate is None or qty is None:
            return None
        above = ctx.const(calc.get("above")) if calc.get("above") else 0.0
        if above is None:
            return None
        return rate * max(qty - above, 0.0)
    if ctype == "difference":
        a = _operand(calc.get("minuend"), ctx)
        b = _operand(calc.get("subtrahend"), ctx)
        if a is None or b is None:
            return None
        diff = a - b
        return max(diff, 0.0) if calc.get("floor_zero", True) else diff
    if ctype == "tiered":
        qty = ctx.obs_quantity(calc["quantity_observation"])
        if qty is None:
            return None
        above = ctx.const(calc.get("above")) if calc.get("above") else 0.0
        remaining = max(qty - above, 0.0)
        lower = 0.0
        total = 0.0
        for tier in calc["tiers"]:
            up_to = ctx.const(tier.get("up_to")) if tier.get("up_to") else None
            rate = ctx.const(tier["rate"])
            if rate is None:
                return None
            band = remaining if up_to is None else min(remaining, max(0.0, up_to - lower))
            if band > 0:
                total += band * rate
                lower += band
                remaining -= band
            elif up_to is not None:
                lower = up_to
            if remaining <= 0:
                break
        return total
    return None


def evaluate_right(spec, observations: list[dict], period: str, *,
                   evaluated_at=None) -> EvaluationResult:
    if isinstance(spec, dict):
        spec = RightSpec.from_dict(spec)
    now = evaluated_at or datetime.now(timezone.utc).isoformat()
    ctx = _Ctx(spec, observations, period)

    missing = [o for o in (spec.required_observations or [])
               if o not in ctx.by_type]
    result = EvaluationResult(right_id=spec.right_id, period=period,
                              currency=spec.currency, evaluated_at=now)
    if ctx.currency_mismatch:
        result.status = "not_evaluable"
        result.calculation_trace = {"reason": "currency_mismatch"}
        return result
    if ctx.negative_quantity:
        result.status = "not_evaluable"
        result.calculation_trace = {"reason": "negative_quantity"}
        return result
    result.input_observation_ids = [
        o.get("observation_id") for o in ctx.by_type.values()
        if o.get("observation_id")]
    if missing:
        result.status = "not_evaluable"
        result.missing_observations = missing
        result.calculation_trace = {"missing_observations": missing}
        return result

    triggered = _eval_trigger(spec.trigger or {"op": "event_exists",
                                               "observation": ""}, ctx)
    if triggered is None:
        result.status = "not_evaluable"
        result.missing_observations = [k for k, v in ctx.trace.items()
                                       if v == "missing observation"]
        result.calculation_trace = {"trigger": ctx.trace}
        result.triggered = None
        return result
    result.triggered = triggered
    if not triggered:
        result.status = "not_triggered"
        result.expected_amount = 0.0
        result.recoverable_amount = 0.0
        result.calculation_trace = {"trigger": ctx.trace}
        return result

    expected = _calc_amount(spec.calculation or {"type": "none"}, ctx)
    if expected is None:
        result.status = "error"
        result.calculation_trace = {"trigger": ctx.trace,
                                    "calculation": "could not resolve operands"}
        return result
    expected = quantize(expected, spec.currency)

    actual = 0.0
    if spec.actual_observation:
        actual = ctx.obs_amount(spec.actual_observation)
        if actual is None:
            if spec.actual_observation_optional:
                actual = 0.0
            else:
                result.status = "not_evaluable"
                result.missing_observations = [spec.actual_observation]
                result.calculation_trace = {"trigger": ctx.trace,
                                            "calculation": expected}
                return result
    result.status = "evaluated"
    result.expected_amount = expected
    result.actual_amount = quantize(actual, spec.currency)
    result.recoverable_amount = quantize(max(expected - actual, 0.0), spec.currency)
    result.calculation_trace = {
        "trigger": ctx.trace,
        "calculation": {"type": spec.calculation.get("type"),
                        "expected": expected, "actual": actual,
                        "recoverable": result.recoverable_amount},
    }
    return result
