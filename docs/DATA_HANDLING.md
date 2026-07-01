# Recoup — Data Handling Statement

Plain-language summary of what Recoup reads, computes, and stores.

## What we read
- **Contracts you upload** (PDF/DOCX/TXT or structured entry) to extract entitlements:
  committed minimums, included units, overage rates, discounts, and escalators.
- **Your Stripe account — read-only.** We read customers, subscriptions, invoices,
  line items, metered usage, and coupons/discounts. Recoup never creates, edits, or
  deletes anything in your Stripe account. Use a restricted, read-only key.

## What we compute
- All dollar figures are computed deterministically in Python from your contract
  terms and billing data. The language model is used only to ground findings in your
  contract text and to draft memos — it never invents numbers.
- Anything we cannot map with high confidence (< 0.85) or that is missing required
  input is flagged `needs_review` for a human, never silently assumed.

## Where it's stored
- Per-tenant in Google Firestore, isolated under `accounts/{account_id}/...`. One
  account cannot see another's data.
- Contract terms, usage, invoices, findings, and an append-only audit log of every
  approve/reject/recover decision.
- Per-tenant connector Stripe keys live in Google Secret Manager as
  `recoup-connector-{account_id}`.
- Sample mode stores nothing — it runs entirely on an in-memory synthetic book.

## Secrets
- Stripe and Firebase credentials are provided via environment variables / Secret
  Manager and are never written to the repository, logs, or Firestore.
- Recoup's own success-fee billing uses a **separate** Stripe account from the
  read-only key used to read your data.

## Authentication
- Access requires a verified Firebase identity. The backend derives your `account_id`
  from the verified token; there is no shared or mock login in production.
