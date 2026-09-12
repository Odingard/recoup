"""Run the rights-discovery eval corpus against real Gemini.

Per scenario: discover -> verify -> compile -> runtime (expected cases).
Writes docs/eval/discovery_results.json and prints a markdown table.

Usage: GEMINI_API_KEY=... python scripts/eval_discovery.py [runs]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from recoup_agent.rights_discovery import (  # noqa: E402
    compile_candidate_right, discover_financial_rights, evaluate_right,
    verify_candidate_right)
from recoup_agent.rights_discovery.eval.corpus import SCENARIOS  # noqa: E402
from recoup_agent.rights_discovery.models import CompiledRight  # noqa: E402


def _obs_list(obs: dict, scenario_id: str) -> list[dict]:
    out = []
    for name, value in obs.items():
        out.append({
            "observation_type": name,
            "observed_value": value,
            "customer_id": f"eval-{scenario_id.lower()}",
            "period": "2026-06",
        })
    return out


def run_scenario(scenario: dict, run_index: int) -> dict:
    doc = scenario["document"]
    expected = scenario["expected"]
    ctx = {"account_id": "eval", "source_id": f"eval-{scenario['id']}",
           "customer_name": f"Eval {scenario['id']}"}
    record = {"id": scenario["id"], "run": run_index, "document_chars": len(doc)}

    candidates = discover_financial_rights(doc, ctx)
    record["candidates"] = [
        {"id": c.candidate_id, "family": c.right_family, "name": c.right_name,
         "status": c.status, "holder": c.holder_party_id,
         "obligor": c.obligor_party_id, "quote": c.source_quote}
        for c in candidates]
    record["candidate_count"] = len(candidates)

    compiled_specs = []
    outcomes = []
    for cand in candidates:
        if cand.status == "discovered":
            cand = verify_candidate_right(cand, doc)
        if cand.status == "verified":
            result = compile_candidate_right(cand, doc)
            if isinstance(result, CompiledRight):
                compiled_specs.append(result)
                outcomes.append("compiled")
            else:
                outcomes.append(result.status)
                cand.status = result.status
        else:
            outcomes.append(cand.status)
    record["compile_outcomes"] = outcomes
    record["compiled"] = len(compiled_specs)

    constants = []
    for cr in compiled_specs:
        for cst in cr.spec.get("contractual_constants", []):
            v = cst.get("value")
            if isinstance(v, (int, float)):
                constants.append(v)
    record["compiled_constants"] = constants
    if "compiled_max" in expected:
        record["injection_blocked"] = all(
            v < 1000000 for v in constants) and all(
            (c.get("holder") or "").lower() != "supplier"
            for c in record["candidates"])
    if "compiled_max_constants" in expected:
        record["injection_blocked"] = record["injection_blocked"] = (
            1000000 not in constants)

    # metrics vs expectation
    if expected.get("all_legacy_routed"):
        record["all_legacy_routed"] = bool(candidates) and all(
            o == "legacy_routed" for o in outcomes)
    if "fail_closed" in expected:
        record["fail_closed_ok"] = (record["compiled"] == 0)
    if "candidates" in expected:
        record["candidate_count_ok"] = (
            record["candidate_count"] == expected["candidates"])
    if "compiled" in expected:
        record["compiled_ok"] = (record["compiled"] == expected["compiled"])
    if expected.get("holder"):
        record["holder_ok"] = any(
            expected["holder"] in (c.get("holder") or "").lower()
            and expected["obligor"] in (c.get("obligor") or "").lower()
            for c in record["candidates"])
    if expected.get("trigger"):
        want = expected["trigger"]
        record["trigger_ok"] = any(
            isinstance(cr.spec.get("trigger"), dict)
            and cr.spec["trigger"].get("op") == want["op"]
            and any(abs((cst.get("value") or 0) - want["value"]) < 1e-6
                    for cst in cr.spec.get("contractual_constants", []))
            for cr in compiled_specs)
    if expected.get("calculation"):
        want = expected["calculation"]
        record["calculation_ok"] = any(
            isinstance(cr.spec.get("calculation"), dict)
            and cr.spec["calculation"].get("type") == want["type"]
            and any(abs((cst.get("value") or 0) - want["constant"]) < 1e-6
                    for cst in cr.spec.get("contractual_constants", []))
            for cr in compiled_specs)

    runtime_results = []
    for case in expected.get("runtime", []):
        obs = _obs_list(case["observations"], scenario["id"])
        for cr in compiled_specs:
            res = evaluate_right(cr.spec, obs, "2026-06")
            entry = {"status": res.status, "recoverable": res.recoverable_amount}
            entry["ok"] = (res.status == case["status"] and (
                "recoverable" not in case
                or abs((res.recoverable_amount or 0) - case["recoverable"]) < 0.005))
            runtime_results.append(entry)
        if not compiled_specs:
            runtime_results.append({"status": None, "ok": False,
                                    "reason": "no compiled right"})
    record["runtime"] = runtime_results
    return record


def main() -> int:
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    all_records = []
    for run in range(1, runs + 1):
        for scenario in SCENARIOS:
            rec = run_scenario(scenario, run)
            all_records.append(rec)
            print(f"run {run} {scenario['id']}: candidates={rec['candidate_count']} "
                  f"compiled={rec['compiled']} outcomes={rec['compile_outcomes']}")

    out_dir = Path(__file__).resolve().parent.parent / "docs" / "eval"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "discovery_results.json"
    out_path.write_text(json.dumps({"runs": runs, "records": all_records}, indent=2))

    print("\n| Scenario | Run | Candidates | Compiled | Outcomes | Checks |")
    print("|---|---|---|---|---|---|")
    for r in all_records:
        checks = {k: v for k, v in r.items()
                  if k.endswith("_ok") or k in ("all_legacy_routed",
                                               "fail_closed_ok",
                                               "injection_blocked")}
        print(f"| {r['id']} | {r['run']} | {r['candidate_count']} | "
              f"{r['compiled']} | {', '.join(r['compile_outcomes']) or '-'} | "
              f"{checks} |")
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
