"""Validation track 5: deterministic runtime property/fuzz.

Seeded random RightSpecs across all five calculation primitives; results must
be identical on re-evaluation and match an independent reference
implementation written here in the test.
"""
import random

import pytest

from recoup_agent.rights_discovery import evaluate_right

PERIOD = "2026-06"


def _spec(constants, trigger, calc, required, actual_obs=None,
          actual_optional=False):
    return {
        "spec_version": "1.0", "right_id": "r-fuzz",
        "right_family": "fuzz", "holder_party_id": "C", "obligor_party_id": "P",
        "trigger": trigger, "calculation": calc,
        "required_observations": required,
        "contractual_constants": constants,
        "currency": "USD",
        "actual_observation": actual_obs,
        "actual_observation_optional": actual_optional,
    }


def _obs(name, value, period=PERIOD):
    return {"type": name, "value": value, "period": period}


def _const_ref(name):
    return {"constant": name}


def _ref_calc_expected(spec, obs):
    """Independent reference implementation of the calc grammar."""
    consts = {c["name"]: float(c["value"])
              for c in spec["contractual_constants"]}
    amounts = {o["type"]: float(o["value"]) for o in obs
               if str(o.get("period", "")).startswith(PERIOD)}

    def cval(ref):
        return consts.get(ref["constant"]) if isinstance(ref, dict) else None

    calc = spec["calculation"]
    t = calc["type"]
    if t == "fixed_amount":
        return cval(calc["amount"])
    if t == "percentage_of":
        rate = cval(calc["rate"])
        base = amounts.get(calc["base_observation"])
        return None if base is None else rate * base
    if t == "per_unit":
        rate = cval(calc["rate"])
        qty = amounts.get(calc["quantity_observation"])
        if rate is None or qty is None:
            return None
        above = cval(calc["above"]) if calc.get("above") else 0.0
        if above is None:
            return None
        return rate * max(qty - above, 0.0)
    if t == "difference":
        def operand(ref):
            if "constant" in ref:
                return cval(ref)
            return amounts.get(ref["observation"])
        a, b = operand(calc["minuend"]), operand(calc["subtrahend"])
        if a is None or b is None:
            return None
        diff = a - b
        return max(diff, 0.0) if calc.get("floor_zero", True) else diff
    if t == "tiered":
        qty = amounts.get(calc["quantity_observation"])
        if qty is None:
            return None
        above = cval(calc["above"]) if calc.get("above") else 0.0
        remaining = max(qty - above, 0.0)
        lower = total = 0.0
        for tier in calc["tiers"]:
            up_to = cval(tier["up_to"]) if tier.get("up_to") else None
            rate = cval(tier["rate"])
            band = remaining if up_to is None else min(
                remaining, max(0.0, up_to - lower))
            if band > 0:
                total += band * rate
                lower += band
                remaining -= band
            elif up_to is not None:
                lower = up_to
            if remaining <= 0:
                break
        return total
    raise AssertionError(t)


def _random_spec(rng: random.Random):
    kind = rng.choice(["fixed_amount", "percentage_of", "per_unit",
                       "difference", "tiered"])
    consts = [{"name": "thr", "value": round(rng.uniform(0, 100), 2),
               "kind": "threshold"}]
    obs_names = ["obs_a", "obs_b", "qty"]
    trigger = {"op": rng.choice(["gt", "gte", "lt", "lte"]),
               "observation": rng.choice(obs_names),
               "value": _const_ref("thr")}
    if kind == "fixed_amount":
        consts.append({"name": "amt", "value": round(rng.uniform(1, 50000), 2),
                       "kind": "amount"})
        calc = {"type": "fixed_amount", "amount": _const_ref("amt")}
    elif kind == "percentage_of":
        consts.append({"name": "rate", "value": round(rng.uniform(0, 0.5), 4),
                       "kind": "rate"})
        calc = {"type": "percentage_of", "rate": _const_ref("rate"),
                "base_observation": "obs_b"}
    elif kind == "per_unit":
        consts.append({"name": "rate", "value": round(rng.uniform(0.1, 9), 2),
                       "kind": "rate"})
        consts.append({"name": "cap", "value": rng.randrange(0, 500),
                       "kind": "quantity"})
        calc = {"type": "per_unit", "rate": _const_ref("rate"),
                "quantity_observation": "qty", "above": _const_ref("cap")}
    elif kind == "difference":
        calc = {"type": "difference",
                "minuend": {"observation": "obs_a"},
                "subtrahend": {"observation": "obs_b"},
                "floor_zero": rng.random() < 0.5}
    else:
        consts.append({"name": "t1_rate", "value": 0.10, "kind": "rate"})
        consts.append({"name": "t2_rate", "value": 0.20, "kind": "rate"})
        consts.append({"name": "t1_cap", "value": 100, "kind": "quantity"})
        calc = {"type": "tiered", "quantity_observation": "qty",
                "tiers": [{"up_to": _const_ref("t1_cap"),
                           "rate": _const_ref("t1_rate")},
                          {"rate": _const_ref("t2_rate")}]}
    spec = _spec(consts, trigger, calc,
                 required=list({trigger["observation"],
                                *(["obs_b"] if kind == "percentage_of" else []),
                                *(["obs_b"] if kind == "difference" else []),
                                *(["qty"] if kind in ("per_unit", "tiered")
                                  else [])}))
    return spec


def test_seeded_fuzz_matches_reference_and_is_deterministic():
    rng = random.Random(20260911)
    checked = 0
    for _ in range(300):
        spec = _random_spec(rng)
        obs = [
            _obs("obs_a", round(rng.uniform(0, 200), 2)),
            _obs("obs_b", round(rng.uniform(0, 100000), 2)),
            _obs("qty", rng.randrange(0, 1000)),
        ]
        # ensure every required observation is present
        for name in spec["required_observations"]:
            if name not in {o["type"] for o in obs}:
                obs.append(_obs(name, 1.0))
        r1 = evaluate_right(spec, obs, PERIOD,
                            evaluated_at="2026-07-01T00:00:00Z")
        r2 = evaluate_right(spec, obs, PERIOD,
                            evaluated_at="2026-07-01T00:00:00Z")
        assert r1.to_dict() == r2.to_dict()
        if r1.status == "evaluated":
            expected = _ref_calc_expected(spec, obs)
            assert expected is not None
            assert abs(r1.expected_amount - round(expected, 2)) < 0.005
            assert r1.recoverable_amount == \
                round(max(r1.expected_amount - (r1.actual_amount or 0.0), 0.0), 2)
            checked += 1
        elif r1.status == "not_triggered":
            assert r1.recoverable_amount == 0.0
        else:
            assert r1.status in ("not_evaluable", "error")
    assert checked > 100  # the fuzz actually exercised evaluated paths


def test_missing_observation_not_evaluable():
    spec = _spec(
        [{"name": "thr", "value": 90, "kind": "threshold"},
         {"name": "amt", "value": 1000, "kind": "amount"}],
        {"op": "lt", "observation": "obs_a", "value": _const_ref("thr")},
        {"type": "fixed_amount", "amount": _const_ref("amt")},
        required=["obs_a"])
    r = evaluate_right(spec, [], PERIOD)
    assert r.status == "not_evaluable"
    assert r.recoverable_amount in (None, 0.0)
    assert "obs_a" in r.missing_observations


def test_false_trigger_not_triggered():
    spec = _spec(
        [{"name": "thr", "value": 90, "kind": "threshold"},
         {"name": "amt", "value": 1000, "kind": "amount"}],
        {"op": "lt", "observation": "obs_a", "value": _const_ref("thr")},
        {"type": "fixed_amount", "amount": _const_ref("amt")},
        required=["obs_a"])
    r = evaluate_right(spec, [_obs("obs_a", 99.9)], PERIOD)
    assert r.status == "not_triggered"
    assert r.recoverable_amount == 0.0


def test_wrong_period_observation_not_evaluable():
    spec = _spec(
        [{"name": "thr", "value": 90, "kind": "threshold"},
         {"name": "amt", "value": 1000, "kind": "amount"}],
        {"op": "lt", "observation": "obs_a", "value": _const_ref("thr")},
        {"type": "fixed_amount", "amount": _const_ref("amt")},
        required=["obs_a"])
    r = evaluate_right(spec, [_obs("obs_a", 1.0, period="2026-05")], PERIOD)
    assert r.status == "not_evaluable"
