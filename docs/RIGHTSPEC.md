# RightSpec — the compiled right grammar

A `RightSpec` is the deterministic compilation of a verified AI-discovered
financial right. It is data, not code: a closed grammar validated by
`recoup_agent.rights_discovery.compiler` and executed by
`recoup_agent.rights_discovery.runtime`. Neither module imports an LLM
library; nothing in a spec is `eval`'d.

## Grammar

```json
{
  "spec_version": "1.0",
  "right_id": "right_<sha256|20>",
  "right_family": "service_level_credit",
  "holder_party_id": "Customer",
  "obligor_party_id": "Provider",
  "trigger": { "op": "lt", "observation": "monthly_uptime",
               "value": {"constant": "sla_threshold"} },
  "calculation": { "type": "fixed_amount",
                   "amount": {"constant": "credit_amount"} },
  "required_observations": ["monthly_uptime"],
  "contractual_constants": [
    {"name": "sla_threshold", "value": 99.95, "kind": "threshold",
     "currency": null, "evidence_ref": "ev_..."},
    {"name": "credit_amount", "value": 15000.0, "kind": "amount",
     "currency": null, "evidence_ref": "ev_..."}
  ],
  "currency": "USD",
  "effective_from": null, "effective_until": null,
  "evidence_refs": ["ev_..."],
  "actual_observation": "credit_received",
  "actual_observation_optional": false
}
```

### Trigger operators (allowlist)

`eq`, `neq`, `gt`, `gte`, `lt`, `lte` — `observation` vs `{"constant": name}`.
`between` — `low`/`high` constant refs. `and`/`or` — non-empty `children`
(recursive). `event_exists` — an observation of that type exists for the
period. `date_reached`/`date_before` — constant date vs period end.
`observed_date_before` / `observed_date_on_or_before` / `observed_date_after`
/ `observed_date_on_or_after` — the observation's own date (read from its
`date`, `observed_at`, or `value` key, ISO) vs a `date`-kind constant.

Every numeric operand is `{"constant": <name>}` referencing a declared
`ContractualConstant`. Inline literals are rejected — this is what makes
constant grounding enforceable.

### Calculation primitives (allowlist)

| type | required fields | result |
|---|---|---|
| `fixed_amount` | `amount` const | the constant |
| `percentage_of` | `rate` const, `base_observation` | rate × base |
| `per_unit` | `rate` const, `quantity_observation`, optional `above` const | rate × (qty − above, floored at 0) |
| `difference` | `minuend`, `subtrahend` (const or `{"observation": name}` refs), `floor_zero` | minuend − subtrahend (floored at 0 unless `floor_zero: false`) |
| `tiered` | `quantity_observation`, `tiers` (`rate`/`up_to` consts), optional `above` | tiered marginal sum |
| `volume_tiered` | same shape as `tiered` | entire (qty − `above`) priced at the single tier whose band contains it |
| `banded_percentage_of` | `base_observation`, `band_observation`, `bands` (`rate`/`up_to` consts) | rate of the band containing band_observation's value × base amount |
| `min_of` / `max_of` | `operands` (2–4 const or observation refs) | min/max of resolved operands (None if any operand is missing) |
| `none` / `unsupported` | — | compile failure (`unsupported`) |

Every primitive may carry `cap` and/or `floor` constant refs: after the
primitive runs, `amount = min(amount, cap)` then `amount = max(amount, floor)`.
Tiers/bands are bounded by `MAX_TIERS` (32), operands by `MAX_OPERANDS` (4);
specs exceeding compiler limits (`MAX_CONSTANTS` 64, `MAX_TRIGGER_DEPTH` 8,
`MAX_TRIGGER_NODES` 64, `MAX_QUOTE_CHARS` 20000, `MAX_EVIDENCE_REFS` 32,
`MAX_REQUIRED_OBSERVATIONS` 32, `MAX_NAME_CHARS` 128) are `rejected` before
any grounding work.

### Constant grounding

The AI reports constants as written (`"$15,000"`, `"99.95%"`, `"March 1, 2026"`).
At compile time each is normalized (`amount`/`rate` strip `$`/`,`; a
`percentage` like `5%` becomes `0.05`; a `threshold` keeps its literal value
since observations report in the same units; dates parse to ISO) and must
appear verbatim — numerically — inside `candidate.source_quote`. A constant
that is not in the quote fails the compile (`needs_review`).

Currency: `USD` is recorded when `$` appears in the quote. If the right has
monetary constants (`amount`/`rate`) and the quote contains no `$`, the
compile fails `needs_review`; rights whose constants are all percentages,
quantities, or thresholds are currency-neutral and default to USD.

## Worked examples

**B — SLA credit.** `"If monthly service availability falls below 99.95%,
Customer shall receive a service credit of $15,000"` → trigger
`lt monthly_uptime {const: sla_threshold=99.95}`, calculation
`fixed_amount {const: credit_amount=15000}`. At `monthly_uptime=99.72`,
`credit_received=0` → `evaluated`, recoverable `15000.00`. At `99.99` →
`not_triggered`.

**C — late delivery.** `"more than 10 days ... credit equal to 5% of the
affected invoice amount"` → trigger `gt days_late {const: 10}`, calculation
`percentage_of {rate: 0.05, base_observation: affected_invoice_amount}`.
At `days_late=14`, `affected_invoice_amount=40000` → `2000.00`.

**D — rebate.** `"above the annual commitment of 100,000 units ... rebate of
$2.25 per unit"` → trigger `gt units_purchased {const: 100000}`, calculation
`per_unit {rate: 2.25, above: 100000}`. At `units_purchased=120000` →
`45000.00`.

**J — no observations.** B's spec with no `monthly_uptime` observation for the
period → `not_evaluable` with `missing_observations=["monthly_uptime"]`;
the right stays compiled and active — the gap is observability, not validity.

## Failure modes (all fail closed)

| Input | Outcome |
|---|---|
| Candidate not verified | `needs_review` |
| Legacy family/name | `legacy_routed` (reconciliation.py owns it) |
| Quote absent from document | `unsupported` at discovery |
| Unknown operator (`exec`, `regex`, …) | `unsupported` |
| Undeclared constant reference | `unsupported`/`needs_review` |
| Constant value absent from quote | `needs_review` |
| `calculation.type` unknown/none | `unsupported` |
| Empty `required_observations` | `needs_review` |
| Monetary constant without `$` in quote | `needs_review` |
| Missing observation at runtime | `not_evaluable` |
| Spec over size limits | `rejected` at compile; `error`/`spec_limits_exceeded` at runtime |
| Invalid numeric literal (non-ASCII, exponents, expressions) | `needs_review` |
| Negative constant where kind forbids it (rate/percentage/quantity) | `needs_review` |
| Model echoes a different amount | overwritten with deterministic values |

---
Architecture: James · Security review: Mark · QA: Jamie · PM: Michael
