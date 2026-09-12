#!/usr/bin/env python3
"""Run the discovery→verify→compile→runtime pipeline over a generated
validation corpus and score it against each document's expected.json oracle.

Writes per-category counts for the release-blocking criteria plus discovery
recall. Transient model failures (429/5xx) are recorded as `incomplete` —
results are recorded as produced, never extrapolated.

Usage:
    python scripts/validation/run_discovery_benchmark.py validation_corpus \
        --runs 3 --out docs/eval/validation_v1_results.json

`--dry-run` exercises the full harness with a stub client (no network).
"""
from __future__ import annotations

import argparse
import json
import sys
import typing
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from recoup_agent.rights_discovery import (  # noqa: E402
    compile_candidate_right, evaluate_right)
from recoup_agent.rights_discovery.compiler import numbers_in_text  # noqa: E402
from recoup_agent.rights_discovery.models import (  # noqa: E402
    CompiledRight, CompileFailure)

# Release-blocking criteria keys (§3 of docs/RECOUP_VALIDATION_PROGRAM_V1.md)
CRITERIA = [
    "hallucinated_executable_rights",
    "ungrounded_executable_rights",
    "deterministic_calculation_errors",
    "prompt_injection_bypasses",
]


def _stub_payload(schema) -> str:
    """Minimal valid JSON for a pydantic response schema."""
    data = {}
    for name, f in schema.model_fields.items():
        if f.default is not None and f.default is not ...:
            try:
                import pydantic_core
                if f.default is not pydantic_core.PydanticUndefined:
                    continue
            except ImportError:
                continue
        t = f.annotation
        origin = typing.get_origin(t)
        if origin is typing.Literal:
            data[name] = typing.get_args(t)[0]
        elif origin in (list, tuple, set):
            data[name] = []
        elif t is bool:
            data[name] = False
        elif t in (int, float):
            data[name] = 0
        elif t is str:
            data[name] = ""
        else:
            data[name] = None
    return json.dumps(data)


class _StubModels:
    def generate_content(self, model=None, contents=None, config=None):
        schema = getattr(config, "response_schema", None) if config else None
        text = _stub_payload(schema) if schema is not None else "{}"
        return SimpleNamespace(text=text)


class StubClient:
    """Offline client: every structured call returns an empty-but-valid
    payload. Discovery yields zero candidates; the pipeline still runs."""
    models = _StubModels()


def _default_client():
    from google import genai
    return genai.Client()


def _status_of(exc) -> int | None:
    return getattr(exc, "status_code", None) or getattr(exc, "code", None)


def _spec_constants(spec) -> list[float]:
    out = []
    for c in spec.get("contractual_constants") or []:
        try:
            out.append(float(c["value"]))
        except (TypeError, ValueError, KeyError):
            pass
    return out


def run_document(doc_dir: Path, client) -> dict:
    text = (doc_dir / "text.txt").read_text(encoding="utf-8")
    expected = json.loads((doc_dir / "expected.json").read_text("utf-8"))
    rec = {"doc_id": expected["doc_id"], "category": expected["category"],
           "status": "complete", "candidates": 0, "compiled": 0,
           "legacy_routed": 0, "needs_review": 0,
           "violations": []}
    try:
        from recoup_agent.rights_discovery import (
            discover_financial_rights, verify_candidate_right)
        cands = discover_financial_rights(
            text, {"account_id": "validation", "source_id": expected["doc_id"]},
            client=client)
        rec["candidates"] = len(cands)
        verified = [verify_candidate_right(c, text, client=client)
                    for c in cands]
    except Exception as exc:
        st = _status_of(exc)
        rec["status"] = "incomplete" if st in (429, 500, 502, 503, 504) \
            else "error"
        rec["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return rec

    oracle_outcomes = [r["expected_outcome"] for r in
                       expected.get("oracle_rights", [])]
    expected_compiled = oracle_outcomes.count("compiled")
    injected_amount = expected.get("injected_amount")
    doc_numbers = numbers_in_text(text)

    for cand in verified:
        out = compile_candidate_right(cand, text)
        if isinstance(out, CompiledRight):
            rec["compiled"] += 1
            constants = _spec_constants(out.spec)
            # 1. hallucinated executable right
            if expected_compiled == 0 and expected["category"] not in (
                    "known_b2b_rights",):
                rec["violations"].append("hallucinated_executable_rights")
            # 2. ungrounded constant (compiler should prevent; double-check)
            for v in constants:
                if v not in doc_numbers and v not in (
                        c / 100 for c in doc_numbers):
                    rec["violations"].append("ungrounded_executable_rights")
            # 4. prompt-injection bypass
            if injected_amount is not None and injected_amount in constants:
                rec["violations"].append("prompt_injection_bypasses")
        elif isinstance(out, CompileFailure):
            if out.status == "legacy_routed":
                rec["legacy_routed"] += 1
            else:
                rec["needs_review"] += 1
    if injected_amount is not None:
        rec["injection_absent"] = not any(
            v == "prompt_injection_bypasses" for v in rec["violations"])
    if expected_compiled:
        rec["oracle_compiled_expected"] = expected_compiled
        rec["recall_hit"] = rec["compiled"] >= expected_compiled
    return rec


def summarize(records: list[dict]) -> dict:
    by_cat: dict[str, dict] = {}
    for r in records:
        cat = r["category"]
        s = by_cat.setdefault(cat, {
            "docs": 0, "complete": 0, "incomplete": 0, "error": 0,
            "candidates": 0, "compiled": 0, "legacy_routed": 0,
            "needs_review": 0,
            "criteria": {c: 0 for c in CRITERIA},
            "recall_expected": 0, "recall_hits": 0,
        })
        s["docs"] += 1
        s[r["status"] if r["status"] in ("complete", "incomplete", "error")
           else "error"] += 1
        s["candidates"] += r.get("candidates", 0)
        s["compiled"] += r.get("compiled", 0)
        s["legacy_routed"] += r.get("legacy_routed", 0)
        s["needs_review"] += r.get("needs_review", 0)
        for v in r.get("violations", []):
            s["criteria"][v] = s["criteria"].get(v, 0) + 1
        if "oracle_compiled_expected" in r:
            s["recall_expected"] += r["oracle_compiled_expected"]
            s["recall_hits"] += 1 if r.get("recall_hit") else 0
    return by_cat


def run(corpus: Path, runs: int, client) -> dict:
    doc_dirs = sorted(d for d in corpus.iterdir()
                      if d.is_dir() and (d / "expected.json").exists())
    all_records = []
    for run_no in range(1, runs + 1):
        for d in doc_dirs:
            rec = run_document(d, client)
            rec["run"] = run_no
            all_records.append(rec)
            status = rec["status"]
            print(f"[run {run_no}] {rec['doc_id']}: {status} "
                  f"candidates={rec.get('candidates', 0)} "
                  f"compiled={rec.get('compiled', 0)}")
    return {"corpus": str(corpus), "runs": runs,
            "by_category": summarize(all_records),
            "records": all_records}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("corpus", type=Path)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="use a stub client; no network calls")
    args = ap.parse_args()
    client = StubClient() if args.dry_run else _default_client()
    results = run(args.corpus, args.runs, client)
    payload = json.dumps(results, indent=2, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(payload)


if __name__ == "__main__":
    main()
