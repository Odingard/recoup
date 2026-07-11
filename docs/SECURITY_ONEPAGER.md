# Recoup Security One-Pager

Recoup is a hosted, multi-tenant SaaS for recovering B2B revenue leakage. Buyers do
not install software; they sign in, install the Recoup Stripe App, upload contracts,
review findings, and approve any action before billing happens.

## What we read
- Contract documents and structured contract terms you upload.
- Stripe billing data using Stripe App OAuth permissions granted during install.
- Stripe enforces the read-only boundary through the manifest-granted permissions.
- We do **not** write to your Stripe account.

## What we store
- Extracted contract terms.
- Usage, invoices, and findings needed to run reconciliation.
- An append-only audit trail of approvals/rejections/recovered actions.
- Per-tenant Stripe connector OAuth credentials in Google Secret Manager.

## Where data lives
- **Firestore**: per-tenant operational data under `accounts/{account_id}/...`.
- **Secret Manager**: per-tenant connector credentials under `recoup-connector-{account_id}`.
- **Encryption**: at rest through Google Cloud managed encryption.

## Subprocessors
- Google Cloud (Cloud Run, Firestore, Secret Manager, Firebase Auth, Vertex AI)
- Stripe
- Google Gemini

## Customer controls
- Revoke the app access from the Stripe dashboard at any time.
- Request deletion of tenant data at any time.
- Nothing is automatically sent to your customers; every corrective action is approval-gated.

## Operational model
- One hosted deployment serves all customers.
- Sample mode is offline and stores nothing.
- The app refuses to boot if Recoup's billing key overlaps a connector credential.

## Rotation note
If a shared `sk_live` was ever committed, logged, or shared, rotate it in the Stripe
Dashboard immediately after splitting billing and connector credentials.
