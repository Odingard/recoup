# Recoup Rights Graph

## 1. Product thesis

Long-term: **Recoup makes sure you receive the money you are entitled to.**

Current wedge: Recoup finds revenue B2B companies are already owed —
contracted minimums, unbilled overage, expired discounts, missed escalators,
underbilled seats — and proves each finding with the signed clause it rests
on.

Public positioning is unchanged. The Rights Graph is the internal
abstraction layer that makes the wedge work today and generalizes tomorrow:
every claim of "you are owed $X" is modeled as a right, its evidence, the
expected state, the observed state, and the discrepancy between them.

```
RECOUP ARCHITECTURAL LAW

AI may determine what a document appears to mean.

AI may identify candidate financial rights.

AI may explain why a discrepancy matters.

AI may draft a recovery action.

AI may NEVER be the authoritative calculator of money owed.

A recoverable financial finding must always resolve to:

AUTHORITATIVE SOURCE
+ PROVENANCE
+ VERIFIED INPUTS
+ DETERMINISTIC CALCULATION
+ OBSERVED REALITY

If any required component is missing:

FAIL CLOSED TO NEEDS_REVIEW.
```

Explicitly deferred domains: healthcare, insurance, royalties, employment,
leases, consumer recovery, marketplace payouts.

## 2. Canonical models

All models are plain dataclasses in `recoup_agent/rights_graph/models.py`;
`to_dict()` produces JSON-safe dicts (enums become values). Optional fields
default to `None`; timestamps are ISO strings. `generated_at` and
`ingestion_timestamp` never participate in id generation.

| Entity | Identity (`stable_id` prefix) | Key fields |
|---|---|---|
| AuthoritySource | `src` | source_type, counterparty_id, effective_date, expiration_date, document_hash |
| EvidenceReference | `ev` | source_id, locator, quoted_text, confidence, extraction_method, content_hash |
| FinancialRight | `rt` | holder_party_id, obligor_party_id, right_type, calculation_rule, calculation_inputs, evidence_refs, status, review_status |
| Observation | `obs` | observation_type, party_id, period, amount, quantity, unit, source_system, external_reference |
| ExpectedState | `exp` | right_id, period, expected_amount, deterministic_rule, calculation_trace |
| Discrepancy | `dsc` | right_id, expected_state_id, actual_observation_ids, expected/actual/recoverable amounts, finding_id |
| RecoveryAction | `act` | discrepancy_id, action_type, status, human_approval_required |
| RecoveryOutcome | `out` | action_id, discrepancy_id, outcome_type, amount_recovered |

`RightsGraph` is the container: `sources, evidence, rights, observations,
expected_states, discrepancies, recovery_actions, outcomes`, plus
`needs_review` and `not_evaluable`. `merge()` unions by entity id — merging
is idempotent (`needs_review` dedupes on content; `not_evaluable` on
(right_id, period)).

## 3. Graph relationships

```
AuthoritySource ──┬─> EvidenceReference  (quoted clause text + hash)
                  └─> FinancialRight     (who owes whom, which term, gated)
                        └─> ExpectedState  (per period: what should have billed)
Observation  (what actually billed/measured, from invoice/usage records)
ExpectedState + Observation ──> Discrepancy  (the delta = recoverable $)
Discrepancy ──> RecoveryAction ──> RecoveryOutcome
```

## 4. Trust boundaries

- `reconciliation.reconcile()` is the only authoritative money calculator.
  The graph copies `monthly_recoverable`, observed amounts, and inputs; it
  never re-derives dollars.
- Extraction (LLM) may populate `term_meta` and `clauses`; its output only
  becomes enforceable when it clears the gating rule in §5.
- `EvidenceReference.content_hash` (sha256 of the quoted text) pins each
  right to an immutable quote.

## 5. AI vs deterministic responsibilities

- AI: reads documents, proposes terms/quotes, drafts recovery language.
- Deterministic: id generation, right gating (all required inputs present +
  quoted evidence + confidence ≥ `CONFIDENCE_THRESHOLD` + required dates
  parse), every dollar figure, the discrepancy lifecycle, the tenant guard.
- When any gating input fails, the right is `inactive`/`needs_review` and no
  discrepancy can attach to it — fail closed, exactly as reconcile() does.

## 6. B2B adapter

`B2BContractAdapter` maps the existing book schema to rights:

| Contract field(s) | Right type | Finding type(s) |
|---|---|---|
| committed_minimum_monthly / minimum_schedule | committed_minimum | unenforced_minimum, missing_base_charge |
| included_units + overage_rate or overage_tiers | usage_overage | unbilled_overage |
| discounts[] with expires | discount_expiration (one per discount) | expired_discount |
| annual_escalator_pct + escalator_effective_date | annual_escalator | missed_escalator |
| committed_seats + seat_price | committed_seat_charge | underbilled_seats |

Supported `source_type` values: `contract` and `contract_amendment` — the
latter is for when a distinct amendment document is ingested; nothing emits
it today. A `minimum_schedule` stays on the `committed_minimum` right's
`metadata.minimum_schedule` (each entry's effective_date/amount/provenance)
and in `calculation_inputs`; it does not synthesize sources.

**Evaluability vs needs_review.** `needs_review` is only for contractual or
authority problems (gating failures, low confidence, missing terms). A right
can be *valid* yet *not evaluable* for a period because the observation it
requires is absent (`REQUIRED_OBSERVATION` in adapter.py: minimum and
escalator rights need a `base_amount_billed`, overage needs `usage_measured`,
discounts need `invoice_issued`, seats need `seat_count_billed`). Those go to
`not_evaluable` (`{right_id, right_type, customer_id, period,
missing_observation, reason}`); the right's `status` stays `active`.

Invoice/usage fields normalize to Observations: `base_charge` →
`base_amount_billed`, `overage_charge` → `overage_billed`, each
`discounts_applied`/`credits_applied` entry → `discount_applied`/`credit_applied`,
`units` → `usage_measured`, `seat_units` → `seat_count_billed`, and
`invoice_issued` per invoice.

### 6b. Novel (AI-discovered) rights

`NovelRightsAdapter` projects compiled `RightSpec`s (see `docs/RIGHTSPEC.md`)
into the same graph. Each `CompiledRight` yields an `AuthoritySource`
(`source_type="contract"`), one `EvidenceReference` carrying the verbatim
`source_quote` (`extraction_method="ai_discovery"`), and a `FinancialRight`
(`status=active`, `review_status=confirmed`,
`metadata.discovery_origin="ai"`). Observations posted via
`POST /api/observations` normalize to `Observation`s; each `EvaluationResult`
from the deterministic runtime yields an `ExpectedState`, and a `Discrepancy`
only when `status=="evaluated"` with `recoverable > 0`. A `not_evaluable`
evaluation lands in `graph.not_evaluable` — same channel as the legacy path.
`RightsGraphService.build_for_book`/`build_for_customer` accept optional
`compiled_rights`/`observations`/`evaluations` and merge this output; legacy
families discovered by AI are routed back to `reconciliation.py` and never
enter this projection, so no clause can be double-counted.

`RecoveryOutcome` additionally carries optional intelligence fields used by
novel recovery cases: `strategy_used`, `counterparty_response`,
`accepted_without_dispute`, `dispute_reason`, `amount_requested`,
`days_to_resolution`, `evidence_strength`, `right_family` (plus the existing
`amount_recovered`).

## 7. Expected vs actual state

For each finding, the adapter creates an `ExpectedState` and a `Discrepancy`
via a per-rule projection (`_PROJECTIONS` in adapter.py). `expected_amount`
and `actual_amount` are `float | None` — never a universal
`actual + recoverable` formula:

| Finding type | expected_amount | actual_amount |
|---|---|---|
| unenforced_minimum / missing_base_charge | engine's `minimum_for_period` value | invoice `base_charge` |
| unbilled_overage | `round(billed_overage + recoverable, 2)` — the engine's own identity `amount = expected_overage − billed_overage` | invoice `overage_charge` (0.0 if none) |
| expired_discount | 0.0 — nothing is owed as a deduction after expiry | the applied discount's observed amount |
| missed_escalator | `None` (engine internals `expected_base`/`baseline` are not recomputed; trace marks `lossless: False`, `see: finding.math`) | `None` |
| underbilled_seats | `None` (seat count × price would be a formula) | `None` (billed-seat quantity in inputs) |

`recoverable_amount` is always `finding["monthly_recoverable"]` and the
trace `formula` is `finding["math"]`. Finding types outside the table are
left unlinked — no discrepancy, no error. `calculation_trace` carries the
machine-readable audit: rule, inputs (contract terms + observed actuals +
period), formula, result, currency, engine, confidence.

Post-term billing (reconcile Rule 5) is review-only by design: it yields a
`needs_review` item and no right, no expected state, no discrepancy.

## 8. Discrepancy lifecycle

Discrepancies are created `open` when an active right and a computed finding
agree. A finding whose supporting right fails gating produces no discrepancy
and a `needs_review` entry instead. The `finding_id` field bridges back to
the legacy finding persisted in Firestore.

## 9. Recovery outcome lifecycle

`RecoveryAction`/`RecoveryOutcome` are projections of the legacy finding
lifecycle — they are rebuilt on read, not stored independently:

Every discrepancy gets exactly one `RecoveryAction`
(`action_type="corrective_invoice"`, `human_approval_required=True`),
including while the finding is still `open`:

| Finding status | RecoveryAction status | RecoveryOutcome type | resolution |
|---|---|---|---|
| open | proposed | (none) | — |
| approved | approved | (none) | — |
| invoiced | executed | (none) | — |
| recovered | executed | recovered (or partially_recovered if paid < expected) | — |
| disputed | executed | disputed | pending (resolved_at None) |
| written_off | closed | written_off | written_off |
| rejected | rejected | rejected | rejected_by_reviewer |

`corrective_invoice.ref/date` map to the action's external_reference and
executed_at; `payment` maps to the outcome's evidence.

## 10. Domain adapter interface

```python
class DomainAdapter(Protocol):
    extract_rights(source_record, account_id)
        -> (AuthoritySource(s), [EvidenceReference], [FinancialRight], [needs_review])
    normalize_observations(source_record, usage, invoice, period, account_id)
        -> [Observation]
    evaluate(source_record, usage, invoice, period, account_id, mode, needs_review)
        -> (findings, RightsGraph)
    build_recovery_context(finding, discrepancy_id)
        -> ([RecoveryAction], [RecoveryOutcome])
```

New domains implement the same four methods; `evaluate` must delegate dollar
math to that domain's authoritative calculator and then *link* results, never
compute them.

## 11. Tenant isolation

Every AuthoritySource, FinancialRight and Observation carries `account_id`.
`RightsGraphService.assert_tenant(graph)` raises `PermissionError` if any
entity's account differs from the service's. The API endpoint
(`GET /api/rights/customers/{customer_id}`) only reads the caller's own
Firestore subcollections via `_load_book(account_id)` and sits behind the
same payment-method gate as the audit report; sample mode (`account_id`
None) always resolves to the offline demo book.

## 12. Audit vs preflight modes

`EvaluationMode.audit` reconciles what already happened.
`EvaluationMode.preflight` is accepted everywhere for forward-looking checks
but currently behaves identically to audit — there is no production
preflight enforcement yet.

## Known limitations

- The graph is rebuilt on read; it is not stored as its own collection.
- Actions/outcomes are projections of the legacy finding lifecycle, not an
  independent state machine.
- `preflight == audit` for now.
- Findings that reconcile computed but whose right failed gating stay
  unlinked (a `needs_review` entry is emitted instead of a discrepancy).
- Active rights that cannot be evaluated for a period (missing observation)
  appear in `not_evaluable`, not `needs_review`.
- `missed_escalator` and `underbilled_seats` projections are lossy by design
  (expected/actual `None`); the authoritative numbers live in the finding's
  `math`/`monthly_recoverable`.
- AI-discovered novel rights enter the graph only after deterministic
  compilation; they inherit the same tenant and gating rules.

---
Architecture: James · Security review: Mark · QA: Jamie · PM: Michael
