# Billing: realized recovered value

Recoup charges a 20% success fee (`SUCCESS_FEE_PCT`) on **realized recovered
value only**. This document defines what counts, how it is recorded, and how
the fee is charged and credited back.

## Realized value

"Recovered Revenue" is financial value the customer actually realizes because
of an approved Finding:

- **Billable bases** (`recoup_agent/billing/realized_value.py: RECOVERY_BASES`):
  `cash_payment`, `settlement`, `refund`, `rebate`, `reimbursement`,
  `contractual_credit` (actually posted or usable), `offset` (actually
  applied), `other_verified_value`.
- **Not billable**: discovering, verifying, or approving a Finding; a promised
  or requested payment; projected or prevented future leakage; a Finding still
  in `open`, `rejected`, or `written_off` status.

## Event model

Billing operates on immutable `RecoveryRealizationEvent`s stored under
`accounts/{account_id}/recovery_events`:

- A **realization** records `realized_value` on a `recovery_basis`; the model
  computes `feeable_value`, `fee_percentage`, `fee_amount` itself — callers
  cannot set fee fields.
- A **reversal** is a new event (`event_type: "reversal"`) with a negative
  `fee_amount`; the original event is never mutated.
- `recovery_event_id` is a deterministic id
  (`stable_id("rev", account, finding, event_type, basis, external_reference or timestamp:value)`),
  so a duplicate `external_reference` is rejected with 409 and can never be
  charged twice.

## Charging and idempotency

Each eligible event is charged once via Stripe with idempotency key
`fee-{account}-{finding}-{recovery_event_id}`. `billing_eligibility()` decides
deterministically whether an event may be charged: the finding must be
approved-or-later and the event must not already be `paid`/`pending`.

Findings recovered before this event model existed are billed once via a
synthesized `other_verified_value` event with `external_reference =
"legacy:{finding_id}"`.

## Reversal example

- $10,000 cash recovery recorded → fee $2,000 charged.
- $4,000 later reversed → reversal event with fee −$800; a Stripe credit note
  of $800 is issued once against the paid fee invoice (idempotency key
  `feeadj-{account}-{reversal_event_id}`). The finding's `recovered_amount`
  becomes $6,000; its status stays `recovered`.
- Reversing the full amount leaves status `recovered` with
  `metadata.fully_reversed = true` — never silently reopened.

## Trust boundary

No AI component ever sets fee fields or decides eligibility. AI discovery
produces findings; fee amounts are computed deterministically from recorded
realizations, and eligibility is a fixed rule check in `realized_value.py`.

## API

- `POST /api/findings/{id}/recovery-events` — record realized value (charges
  the fee when eligible).
- `POST /api/findings/{id}/recovery-events/{event_id}/reverse` — record a
  reversal (credits the fee when it was paid).
- `GET /api/findings/{id}/recovery-events` — list events for a finding.
- `POST /api/findings/{id}/recovered` and `/api/billing/sync-recoveries`
  create `cash_payment` events (external_reference = payment ref / Stripe
  invoice id) so re-runs never double-charge.

## Team

Architecture: James · Security review: Mark · QA: Jamie · PM: Michael
