# Recoup Product Validation Program v1.0

Architecture: James · Security review: Mark · QA: Jamie · PM: Michael

This program validates the feature-frozen Recoup build against the Architectural Law:
a recoverable finding must resolve to AUTHORITATIVE SOURCE + PROVENANCE + VERIFIED INPUTS
+ DETERMINISTIC CALCULATION + OBSERVED REALITY, or fail closed to `needs_review`.

An independent, frozen validation corpus and oracle exist outside this repository.
This program does **not** reference, request, or inspect them. Everything below is
reproducible from this repository alone.

## 0. Baseline freeze

- Target: the `main` SHA recorded in the freeze declaration (see release notes).
- No feature work lands after the freeze. Only validation fixes, each re-run against the full harness.
- Reproduce the offline baseline:

```bash
env -u RECOUP_BILLING_STRIPE_API_KEY .venv/bin/python -m pytest -q --ignore=test_rights_discovery_live.py
```

## 1. Validation tracks

| # | Track | What is proven | Where |
|---|-------|----------------|-------|
| 1 | Legacy regression | Golden totals for every B2B rule (`unenforced_minimum`, `unbilled_overage`, `expired_discount`, `missed_escalator`, `missing_base_charge`, `underbilled_seats`) unchanged to the cent; post-term stays review-only | `test_reconciliation*.py`, `test_golden*.py` |
| 2 | AI discovery benchmark | Recall/precision of `discover_financial_rights` on the synthetic corpus; every miss and false candidate recorded | `scripts/validation/run_discovery_benchmark.py` (live Gemini) |
| 3 | Evidence grounding | Every constant in every compiled RightSpec appears verbatim in the cited quote; ungrounded → `CompileFailure` | `test_validation_compiler_torture.py` |
| 4 | Compiler torture | Malformed schema, unknown operators/primitives, executable content, legacy families, missing dates/currency, nested/oversized triggers all fail closed; no LLM import in `compiler.py`/`runtime.py` | `test_validation_compiler_torture.py` |
| 5 | Deterministic runtime | Property/fuzz: same spec+observations → identical result and trace; missing observation → `not_evaluable`; false trigger → `not_triggered`; arithmetic matches an independent reference implementation in the test | `test_validation_runtime_fuzz.py` |
| 6 | Adversarial AI | Prompt-injection documents never produce a compiled right, never change amounts, never alter status; document text is treated as data | `test_validation_adversarial.py`, corpus category `adversarial` |
| 7 | End-to-end traceability | AuthoritySource → Candidate → CompiledRight → FinancialRight → Observation → ExpectedState → Discrepancy → Finding → RecoveryAction → RecoveryRealizationEvent, all IDs stable and linked | `test_rights_discovery.py`, `test_rights_graph.py` |
| 8 | Tenant isolation / security | Every new collection scoped under `accounts/{id}`; cross-tenant reads return 404/empty; all new endpoints require auth; account deletion removes candidates/compiled rights/observations/recovery events | `test_validation_tenant.py` |
| 9 | Billing | $10k→$2k; partials billed once each; duplicate event → 409, single charge; $4k reversal → −$800 once; every basis; Stripe failure → error then single retry; AI cannot set fee fields | `test_realized_value_billing.py` |
| 10 | Failure / chaos | Gemini 503/timeout/garbage JSON → candidates `needs_review`, no exception reaches the client, no money computed; Firestore write failure → no partial finding persisted | `test_validation_chaos.py` |
| 11 | Complete customer journey | Upload → reconcile → report → approve → corrective invoice → recovery event → fee, on the sample and messy sets | `test_api*.py`, `messy_run/` |
| 12 | Shadow-pilot readiness | Checklist in §4 | manual |

## 2. Synthetic validation corpus (200 documents)

Generated deterministically (seeded) by `scripts/validation/generate_corpus.py` into
`validation_corpus/` (not committed; regenerate with `--seed 20260911`). Each document ships
with an `expected.json` oracle that the generator derives from its own parameters — the
oracle never comes from the model.

| Category | Count | Oracle expectation |
|----------|-------|--------------------|
| known B2B rights | 40 | discovery may find them; compiler must return `legacy_routed`; `reconcile()` totals match oracle |
| novel financial rights | 60 | compiled; runtime result equals oracle amount to the cent |
| multi-right agreements | 20 | every oracle right found; no extra compiled right |
| no-right controls | 20 | zero candidates ≥ 0.85; zero compiled |
| ambiguous | 15 | `needs_review`, zero compiled |
| conflicting / amended | 15 | `needs_review` unless amendment unambiguously supersedes; never two conflicting compiled rights |
| poor OCR / scan | 10 | `needs_review` or correct compile; never a wrong amount |
| adversarial / prompt injection | 20 | zero compiled rights; injected amounts never appear in any spec |

## 3. Release-blocking criteria (all must be zero)

1. Hallucinated executable rights (compiled right with no matching oracle right).
2. Ungrounded executable rights (constant not in cited quote).
3. Deterministic calculation errors (runtime ≠ oracle, to the cent).
4. Prompt-injection bypasses.
5. Unauthorized actions (unauthenticated or cross-tenant success).
6. Tenant-isolation failures.
7. Duplicate success-fee charges.
8. Legacy regressions (any golden total changes).
9. Unsupported rules silently executed (anything outside the RightSpec grammar producing a result).
10. Required CI failing on the freeze SHA.

Discovery **recall** on novel rights is reported, not blocking: a missed right is lost
recovery, not a wrong number. Any false `$` figure is blocking.

## 4. Shadow-pilot readiness checklist

- [ ] Terms of Service jurisdiction placeholders `[STATE]` / `[COUNTY, STATE]` filled (Andre).
- [ ] Live-mode Stripe fee charge exercised once on the Odingard account with a real card.
- [ ] Full three-run Gemini discovery sweep completed without 503 gaps and recorded in `docs/AI_DISCOVERY_EVALUATION.md`.
- [ ] Non-Stripe (QuickBooks/Xero) recoveries recorded as `other_verified_value` events with evidence attached.
- [ ] First real customer's contracts run in shadow mode; every finding hand-checked before any letter is sent.

## 5. Running the program

```bash
# offline (deterministic; required in CI)
env -u RECOUP_BILLING_STRIPE_API_KEY .venv/bin/python -m pytest -q --ignore=test_rights_discovery_live.py

# corpus + live model benchmark (needs GOOGLE_API_KEY / Vertex credentials)
.venv/bin/python scripts/validation/generate_corpus.py --seed 20260911 --out validation_corpus
.venv/bin/python scripts/validation/run_discovery_benchmark.py validation_corpus --runs 3 --out docs/eval/validation_v1_results.json
```

Results are recorded as produced. Incomplete runs are marked incomplete, never
extrapolated.
