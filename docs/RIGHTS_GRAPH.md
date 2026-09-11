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
`needs_review`. `merge()` unions by entity id — merging is idempotent.

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

`minimum_schedule` entries whose effective_date differs from the contract's
(≠ "original term") emit additional `contract_amendment` AuthoritySources.

Invoice/usage fields normalize to Observations: `base_charge` →
`base_amount_billed`, `overage_charge` → `overage_billed`, each
`discounts_applied`/`credits_applied` entry → `discount_applied`/`credit_applied`,
`units` → `usage_measured`, `seat_units` → `seat_count_billed`, and
`invoice_issued` per invoice.

## 7. Expected vs actual state

For each finding, the adapter creates an `ExpectedState` (expected amount =
observed actual + recoverable, or the recoverable itself where the actual is
a quantity, e.g. seats) and a `Discrepancy` linking it to the actual
Observation ids. `calculation_trace` carries the machine-readable audit:
rule, inputs (contract terms + observed actuals + period), formula (the
finding's `math` string), result, currency, engine, confidence.

## 8. Discrepancy lifecycle

Discrepancies are created `open` when an active right and a computed finding
agree. A finding whose supporting right fails gating produces no discrepancy
and a `needs_review` entry instead. The `finding_id` field bridges back to
the legacy finding persisted in Firestore.

## 9. Recovery outcome lifecycle

`RecoveryAction`/`RecoveryOutcome` are projections of the legacy finding
lifecycle — they are rebuilt on read, not stored independently:

| Finding status | RecoveryAction status | RecoveryOutcome type |
|---|---|---|
| open | (none) | (none) |
| approved / invoiced | approved / invoiced | (none yet) |
| recovered | recovered | recovered (or partially_recovered if paid < expected) |
| disputed | disputed | disputed |
| written_off | written_off | written_off |
| rejected | rejected | false_positive |

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
