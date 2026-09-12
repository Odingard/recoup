# AI-Native Rights Discovery — Architecture

Recoup started as a B2B reconciliation engine: extract five known contract terms,
reconcile them deterministically, recover the leakage. This document describes
the second layer: **AI-native discovery of financial rights that the fixed
pipeline does not know about** — service-level credits, late-delivery penalties,
volume rebates, reimbursements, anything a contract grants that costs money
when it is missed.

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

## 1. Operating model

AI participates in four places, and in none of them does it compute money:

1. **Discovery** (`discovery.py`) — reads the contract as untrusted data inside
   `<document>` tags and proposes `CandidateFinancialRight`s, each with a
   verbatim `source_quote`. A candidate whose quote is not found in the
   document (whitespace-normalized) is created `unsupported` — provenance is
   enforced, never trusted.
2. **Verification** (`verifier.py`) — a second, independent model call with no
   shared context re-checks the candidate against the document: is it actually
   a right, is the quote real, were terms invented, is ambiguity present.
   All booleans must pass, both confidences must meet `CONFIDENCE_THRESHOLD`
   (0.85, shared with the extraction pipeline), else `rejected` or
   `needs_review`.
3. **Compilation** (`compiler.py`, no LLM) — deterministic. Routes legacy
   families (minimums, overage, discounts, escalators, seats — including
   alias and name-keyword matches) back to `reconciliation.py` and never
   compiles them. Generic candidates compile to a `RightSpec`: a closed
   grammar of allowlisted trigger operators and calculation primitives where
   every operand is a named `ContractualConstant` grounded verbatim in the
   cited quote. Ungrounded constants, unknown operators, missing
   observations, or ungroundable currency all fail closed.
4. **Runtime** (`runtime.py`, no LLM) — evaluates a `RightSpec` against
   recorded observations for a period. Missing required observations →
   `not_evaluable`. Trigger false → `not_triggered`. Triggered → expected
   amount minus what was already received (`actual_observation`) →
   `recoverable_amount`. There is no `eval()`, no generated code, no
   free-form arithmetic anywhere in the chain.

## 2. Agents

The ADK pipeline is `discovery → reconciliation → investigation →
recovery_strategist → action`:

- `discovery_agent` (`list_contracts`, `discover_rights_for_book`) summarizes
  the book and reports persisted AI-discovered candidates.
- `reconciliation_agent` (`run_reconciliation`, `evaluate_compiled_rights`)
  runs both deterministic evaluators and reports numbers verbatim.
- `investigation_agent` (`get_findings`, `lookup_contract_clause`,
  `build_recovery_case_tool`) grounds findings in clause text; for novel
  findings it builds a recovery case whose amount and calculation are
  overwritten with the deterministic values after the model responds.
- `recovery_strategist_agent` (`recommend_recovery_tool`) picks a route from a
  fixed allowlist (`corrective_invoice`, `credit_request`,
  `reimbursement_request`, `contractual_notice`, `counterparty_inquiry`,
  `manual_review`); off-list answers are forced to `manual_review` and every
  recommendation carries `requires_human_approval=True`.
- `action_agent` is unchanged: drafts, queue, human sign-off before anything
  moves.

## 3. Routing law

`reconciliation.py` remains the **only** calculator for the five legacy
families. A discovered right whose family or name matches them is marked
`legacy_routed` and is never compiled into the generic runtime — the two
systems can never double-count the same clause.

## 4. Financial truth requirements

A novel finding exists only when: the candidate's quote is verbatim in the
document; the verifier confirms right/quote/parties/trigger/calculation with
no invented terms and no blocking ambiguity; every constant in the compiled
spec is grounded in that quote; the trigger fired against recorded
observations; and the recoverable amount came out of the deterministic
runtime. Anything short of that is `needs_review`, `unsupported`, or
`not_evaluable` — never a finding.

Injection attempts inside documents are data, not instructions: the discovery
prompt says so explicitly, anomalies are recorded in `document_anomalies`,
and any candidate the model produces still has to survive verbatim-provenance,
verification, and constant grounding — so an injected "APPROVE $1,000,000"
sentence cannot become a compiled right unless the document itself actually
grants it.

## 5. Trust boundaries

| Surface | Boundary |
|---|---|
| Document text | Untrusted third-party data inside `<document>` tags |
| Candidate | Untrusted AI output inside `<candidate>` tags for verification |
| Compiler/runtime | No LLM libraries importable; closed grammars only |
| Observations | Posted per-tenant via `POST /api/observations`; evaluated per period |
| Findings from novel rights | `type: novel:<family>`, human approval still required downstream |
| Read APIs | Clause-exposing endpoints gated by `_require_unlocked` (card on file) |

---
Architecture: James · Security review: Mark · QA: Jamie · PM: Michael
