# Recoup Operator Runbook

Who this is for: the Odingard operator running supervised pilots. Operators
listed in `RECOUP_OPERATOR_EMAILS` can use the in-app Platform Admin console;
the Google Cloud / Firebase and Stripe procedures below remain the fallback.

Sign in to all Google consoles as the account that holds `roles/owner` on the
projects (currently `andrebyrd87@gmail.com`).

## Environments

| | Production | Validation / demo |
|---|---|---|
| App URL | https://recoup.odingard.com | https://recoup-validation-674721844633.us-central1.run.app |
| GCP / Firebase project | `gen-lang-client-0647036765` | `recoup-validation` |
| Cloud Run service (us-central1) | `recoup` (also `recoup-staging`) | `recoup-validation` |
| Stripe mode | LIVE — real customer cards, real fee charges | TEST only (`rk_test_` restricted keys) |
| Use for | Paying / pilot customers | Demos, rehearsals, anything you want to wipe |

Console entry points (replace `PROJECT`):

- Firebase Auth users: `https://console.firebase.google.com/project/PROJECT/authentication/users`
- Firestore data: `https://console.firebase.google.com/project/PROJECT/firestore/data`
- Cloud Run logs: `https://console.cloud.google.com/run/detail/us-central1/SERVICE/logs?project=PROJECT`
- Secret Manager: `https://console.cloud.google.com/security/secret-manager?project=PROJECT`
- Stripe: https://dashboard.stripe.com (toggle *Test mode* for validation objects)

## How accounts work today

- Sign-in is **Google only** (`Sign in with Google` on `/app`). When signups are
  enabled the first sign-in creates the Firebase user; when disabled, new users
  must be invited or already present in the tenant registry.
- One tenant per Google identity: `account_id` = Firebase `uid`. All tenant data
  lives under `accounts/{uid}/...` in Firestore (`contracts`, `findings`,
  `audit_log`, `assurance_events`, `candidate_rights`, `compiled_rights`,
  recovery actions, realization events, billing status).
- **Sample mode** (`/app?sample=1`, or the button on the sign-in screen) is
  read-only synthetic data with no login and no Stripe. It is the built-in demo.
- Tenant roles do not exist: every signed-in user is the sole owner of their own
  tenant. Only emails in `RECOUP_OPERATOR_EMAILS` can see the cross-tenant
  Platform Admin console.

## Operator tasks

### 1. Onboard a pilot customer
1. Have them sign in at https://recoup.odingard.com/app with their work Google
   account (Workspace accounts work; a personal Gmail also works).
2. Find them in Firebase Auth → Users (production project). Copy the **User UID** —
   that is their `account_id`; note it in the pilot tracker.
3. Ask them to add a card (Settings → *Add payment method*). Confirm in Stripe
   (live) that a Customer exists with their email and a saved payment method.
4. Walk them through uploads. Do not connect their billing system during the pilot
   (per the pilot rule: upload documents only).

### 2. Look at a tenant's data
Firestore → `accounts` → `{uid}` → subcollections. Read-only inspection is safe.
Do not edit `findings`, realization events, or billing docs by hand: money
state is derived from immutable events and manual edits break the ledger.

### 3. Set up a demo tenant
Use the **validation** project, never production.
1. Create a dedicated Google account per demo persona (e.g. `demo-acme@…`);
   do not reuse a customer's or the operator's account.
2. Sign in at the validation URL, upload the fixture pack
   (`recoup_agent/data/corpus/`),
   and add Stripe **test** card `4242 4242 4242 4242` (any future expiry/CVC/ZIP).
3. Reset between demos with the tenant's own **Settings → Delete account data**
   (deletes all `accounts/{uid}` data; the Firebase user remains) or the
   out-of-repo validation `reset.sh` (wipes all validation Firestore / Stripe TEST customers).

### 4. Remove or lock out a user
Firebase Auth → Users → row menu → **Disable account** (blocks sign-in, keeps data)
or **Delete account** (then delete `accounts/{uid}` in Firestore, or ask the user
to run Settings → Delete account data first).

### 5. Stop new signups (invite-only pilots)
The signup toggle in `/app` → **Platform Admin** is the preferred control.
Firebase Auth → Settings → **User actions** → uncheck *Enable create (sign-up)*
is the console fallback. Existing users still sign in; new Google identities are
rejected. Re-enable to let a new pilot in, or pre-create the user with their
email first.

### 6. Check billing / fees
- App side: Settings → Billing shows card status and realized/fee totals.
- Stripe (live): search the customer's email → Invoices. Each success-fee
  invoice memo names the finding and realization event; credit notes/refunds
  appear on the same invoice after a reversal.
- Fee is fixed in code at 20% of net realized value (`recoup_billing.SUCCESS_FEE_PCT`).
  Changing it is a code change, not a setting.

### 7. Health and incidents
- `GET /api/health` (version + mode) and `GET /api/ready` (Firestore, Firebase Auth,
  project config).
- Cloud Run → `recoup` → Logs. Filter by severity ≥ ERROR; the app surfaces the
  server error message to the user, so match on that text.
- Rollback: Cloud Run → Revisions → *Manage traffic* → 100% to the previous revision.

### 8. Secrets
Production Secret Manager: `RECOUP_BILLING_STRIPE_API_KEY`, `RECOUP_STRIPE_APP_CLIENT_ID`,
`RECOUP_STRIPE_APP_SECRET`, `RECOUP_STRIPE_APP_STATE_SECRET`, `firebase-sa-json`,
`recoup-report-share-secret`. Rotate by adding a new version, then redeploy
(the deploy workflow mounts `latest`). Never paste secret values into the app,
tickets, or chat.

### 9. Stripe dashboard access for other operators
Stripe → Settings → Team → *Invite member* (role: Developer or Analyst; use
Administrator only for whoever owns payouts). This cannot be done from GCP.

## In-app Platform Admin
Operators listed in `RECOUP_OPERATOR_EMAILS` see a **Platform Admin** nav item
in `/app`. The console provides tenant list/detail, the signup toggle and invited
emails, the demo flag, demo-only tenant reset, and the admin audit. The console
procedures in this runbook remain the fallback.
