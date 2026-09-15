# Recoup — Operator Guide (Phase 1)

Recoup finds revenue leakage by reconciling contract entitlements against what was
actually billed. Every dollar figure is computed by deterministic Python
(`reconciliation.py`); the LLM only grounds findings in contract language and drafts
memos. Anything low-confidence or unmapped is flagged `needs_review` — never silently
guessed.

## 1. Configuration (all via environment)

See `recoup_agent/.env.example`. Nothing is hardcoded — set these at deploy time.

| Variable | Purpose |
| --- | --- |
| `GOOGLE_CLOUD_PROJECT` | GCP project for Firestore, Secret Manager, and Vertex AI |
| `GOOGLE_GENAI_USE_VERTEXAI` | `TRUE` to use Gemini via Vertex AI |
| `RECOUP_SAMPLE_MODE` | `1` = offline synthetic demo, no auth/Firestore. `0` in production |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Service-account JSON or path (omit on Cloud Run to use ADC) |
| `RECOUP_CONNECTOR_TEST_STRIPE_API_KEY` | Local/dev Stripe App access token fallback for the connector |
| `RECOUP_STRIPE_APP_CLIENT_ID` | OAuth client ID. A Stripe Connect client ID (`ca_…`, Settings → Connect → Onboarding options → OAuth) routes to `connect.stripe.com/oauth/authorize` with `scope=read_only`; a Stripe App client ID routes to the marketplace authorize endpoint |
| `RECOUP_STRIPE_APP_SECRET` | Platform secret key (`sk_live_…`) used as Basic auth when exchanging/refreshing OAuth tokens |
| `RECOUP_STRIPE_APP_REDIRECT_URI` | Public HTTPS OAuth callback URL for the Stripe App manifest |
| `RECOUP_STRIPE_APP_AUTHORIZE_URL` | Override for the OAuth authorize URL (default chosen from the client ID type) |
| `RECOUP_STRIPE_APP_TOKEN_URL` | Stripe OAuth token exchange URL |
| `RECOUP_STRIPE_APP_STATE_SECRET` | Optional signing secret for OAuth state tokens |
| `RECOUP_WEB_BASE_URL` | Public Recoup web app URL used after Stripe redirects back |
| `RECOUP_BILLING_STRIPE_API_KEY` | Dedicated Stripe key used only for Recoup success-fee billing |
| `RECOUP_REPORT_SHARE_SECRET` | HMAC key that signs public report share links; deploy generates it once per project in Secret Manager (`recoup-report-share-secret`). Unset → `POST /api/report/share` returns 503 and sharing is disabled |
| `RECOUP_BILLING_SOURCE` | `stripe` to reconcile from Stripe instead of the synthetic/Firestore book |
| `RECOUP_DOCAI_PROCESSOR` | Document AI processor resource name (`projects/…/locations/{loc}/processors/…`) for scanned-PDF OCR — in the same GCP project, so the deploy workflow enables `documentai.googleapis.com` and grants the runtime SA `roles/documentai.apiUser` itself. Returns per-block/token confidence; unset → Gemini OCR fallback (no confidence signal) |
| `RECOUP_OCR_CONFIDENCE_GATE` | Min OCR confidence for a cited quote to count as verified (default `0.85`). Below it, the term's final confidence is capped at 0.7 and the UI shows "Low OCR confidence" instead of "Verified quote" |
| `PORT` | Server port (Cloud Run injects this) |

### OCR confidence gate

Scanned agreements go through OCR before extraction. The Document AI adapter
(`RECOUP_DOCAI_PROCESSOR`) returns per-block and per-token `layout.confidence`;
the verifier compares the confidence of the blocks overlapping each cited quote
(falling back to the page's mean token confidence) against
`RECOUP_OCR_CONFIDENCE_GATE` (default 0.85). A degraded page that transcribed a
number wrong is therefore capped at 0.7 confidence and surfaces as "Low OCR
confidence" in the Agreements panel instead of "Verified quote". The Gemini OCR
path returns no confidence signal and is unaffected. To disable the gate's
confidence signal entirely, unset `RECOUP_DOCAI_PROCESSOR` — OCR falls back to
the Gemini adapter.

Per-tenant connector OAuth credentials live in **Secret Manager** under
`recoup-connector-{account_id}`. The stored payload includes the access token,
refresh token, and Stripe account metadata; the runtime resolves the current
access token on demand and passes it explicitly as `api_key=`.

## 2. Run locally

```bash
pip install -r requirements.txt

# Offline demo — no credentials required
python -m recoup_agent.pipeline

# API in sample mode
RECOUP_SAMPLE_MODE=1 uvicorn recoup_agent.api:app --port 8001

# Web
cd web && npm install && VITE_API_BASE=http://127.0.0.1:8001/api npm run dev
```

### Health and readiness

- `GET /api/health` — liveness, always 200; body adds `"mode": "sample"|"live"`.
- `GET /api/ready` — unauthenticated readiness probe. Runs the dependency
  checks in `recoup_agent/readiness.py` (deep result cached 10s). In live mode
  `project_config`, `firestore`, and `firebase_auth` are required; billing,
  connector, and Vertex checks are reported but non-blocking. 200 `ready` /
  503 `not_ready`. Responses contain check names and booleans only — never
  secret values.
- Startup: in production mode (anything but `RECOUP_SAMPLE_MODE`) the process
  fails fast on config problems such as a missing `GOOGLE_CLOUD_PROJECT`,
  rather than booting and looking healthy.

## 3. Deploy to Cloud Run

Use the one-time stack from `docs/SECURITY_ONEPAGER.md` / the deployment-security
brief: Cloud Run for the app, Firestore for tenant data, Secret Manager for
per-tenant connector credentials and billing secrets, Firebase Auth for sign-in,
and a custom domain for the hosted SaaS URL.

```bash
gcloud run deploy recoup --source . \
  --region us-central1 \
  --set-env-vars GOOGLE_CLOUD_PROJECT=your-project,GOOGLE_GENAI_USE_VERTEXAI=TRUE
# Provide RECOUP_BILLING_STRIPE_API_KEY via Secret Manager.
```

Store Stripe/Firebase secrets in **Secret Manager** and mount them with
`--set-secrets`; never bake them into the image.

The service serves two surfaces from the same origin: a static marketing page at
`/` and the React app at `/app` (`/app` 307-redirects to `/app/`). The Stripe
OAuth callback stays at `/api/connector/stripe/oauth/callback`; after the
handshake the browser lands on `/app/?stripe_connect=success|error`.

### Custom domain

```bash
gcloud beta run domain-mappings create --service recoup \
  --domain recoup.odingard.com --region us-central1
```

The domain must first be verified in Search Console by the project owner. Then
point DNS at Google: a CNAME `recoup → ghs.googlehosted.com` (in Cloudflare, keep
the record DNS-only / grey cloud).

## 4. Customer onboarding flow

1. **Sign in** with Firebase (Google). The account is isolated in Firestore under
   `accounts/{account_id}/...`.
2. **Upload contracts** — structured form now, or `POST /api/ingest/contract/document`
   with a text-based PDF/DOCX/TXT (Gemini extracts terms with confidence + provenance).
3. **Connect Stripe** — click the Stripe App install flow. Stripe grants Recoup
   read-only access through the manifest; no customer key copy/paste is involved.
4. **Run reconciliation** for a billing period.
5. **Confirm extracted terms** — anything below 0.85 confidence is flagged.
6. **Review findings** — approve or reject each.
7. **Mark recovered** once the money is actually collected.
8. **Billing** — Recoup invoices 20% of *recovered* dollars via its own Stripe account.
   Export findings to CSV anytime.

## 5. Recovery evidence

Findings follow a lifecycle: `open → approved → invoiced → recovered`, with side
states `rejected`, `disputed`, and `written_off`. Illegal transitions return 409.

- `POST /api/findings/{id}/invoiced` records the corrective invoice sent to the
  customer (`invoice_ref`, `invoice_amount`, optional date/URL/note).
- `POST /api/findings/{id}/recovered` requires payment evidence (`paid_amount`,
  optional `paid_date`, `payment_ref`) and stores `recovered_amount`.
- `POST /api/findings/{id}/disputed` and `…/written-off` take an optional reason.

Recoup's 20% success fee is computed on **recorded paid amounts** (`recovered_amount`),
falling back to `monthly_recoverable` only for legacy findings recorded before
payment evidence existed.

### True-up packs

`GET /api/trueup/{customer_id}` (JSON) and `GET /api/trueup/{customer_id}.pdf`
build the collection document the operator sends to their customer: a cover
letter plus a "Schedule of amounts due" (period, item, amount, quoted clause,
calculation). Only `approved`, `invoiced`, and `disputed` findings are included
(`?include_open=1` adds open; sample mode always includes open so the demo
works). `?sender=` sets the sign-off name. No Recoup branding appears in the
pack. Step 6 of the app lists one download per customer.

### Tenant data deletion

`DELETE /api/account/data` with body `{"confirm": "DELETE"}` deletes every
Firestore subcollection under the account (findings, audit_log, usage,
invoices, contracts, anything else present), the account root document, and
the tenant's connector secret in Secret Manager. Returns per-collection
counts. Wrong confirmation returns 400.

## Platform Admin

Set `RECOUP_OPERATOR_EMAILS` to a comma-separated, case-insensitive allowlist
of Google/Firebase emails. Operators see the **Platform Admin** screen in `/app`.
Sample-mode identities are never operators.

Platform data is stored outside tenant `accounts/` trees in the `platform`
namespace: the `settings` document, the `tenants` registry, and the
`admin_audit` subcollection. The registry records account id, email,
first/last seen, and demo state; operator mutations append audit entries.

Endpoints:

- `GET /api/admin/me` — reports whether the signed-in identity is an operator.
- `GET|PUT /api/admin/settings` — controls `signup_enabled` and
  `invited_emails`.
- `GET /api/admin/tenants` and `GET /api/admin/tenants/{account_id}` — tenant
  summaries, recent tenant audit entries, and assurance events.
- `POST /api/admin/tenants/{account_id}/demo` — marks a tenant as demo.
- `POST /api/admin/tenants/{account_id}/reset` — deletes tenant data only when
  the registry marks it `demo`; production customer data still requires the
  customer's own Settings deletion flow.
- `GET /api/admin/audit` — recent operator actions.

When signups are disabled, unknown users must be invited or already present in
the tenant registry; operators always pass. If the platform settings lookup
fails, the signup gate fails open so a Firestore outage does not lock every
customer out.

## 6. Billing gate & fee collection

Recoup charges the operator 20% of dollars actually recovered, collected against a
card on file through Recoup's own Stripe account (`RECOUP_BILLING_STRIPE_API_KEY`).

- `GET /api/billing/status` — `{configured, card_on_file, card_brand, card_last4, success_fee_pct}`.
- `POST /api/billing/setup-session` — creates a hosted Stripe Checkout session in
  `setup` mode; success returns to `RECOUP_WEB_BASE_URL` (or the request origin) at
  `/app/?billing_setup={CHECKOUT_SESSION_ID}`.
- `POST /api/billing/setup-complete` `{session_id}` — verifies the session, attaches
  the payment method as the customer's default, and stores `billing` on the
  Firestore account root doc.
- `POST /api/billing/sync-recoveries` — checks `invoiced` findings whose corrective
  invoice ref starts with `in_` against the tenant Stripe connector; paid invoices
  transition to `recovered` (amounts from Stripe, `verified_via: stripe_connect`)
  and trigger the fee charge.
- `POST /api/billing/charge-success-fee` — charges each recovered finding's card on
  file; without a card it falls back to a mailed Stripe invoice for the total.

While billing is configured and no card is on file, clause proof (`provenance`,
`clause_text`, `math`, `detail`) is redacted on findings and `/api/report`,
`/api/report.pdf`, `/api/report/share`, `/api/trueup/*` and `/api/findings/export`
return HTTP 402. Status transitions stay open. Without `RECOUP_BILLING_STRIPE_API_KEY`
(dev/test) nothing locks; sample mode is never locked.

## 7. Robustness

No bad input returns a 500. Corrupt files, scanned/image PDFs, unsupported formats,
empty Stripe accounts, and missing fields all return a clear, actionable message and
keep going. Low-confidence or unmappable data becomes `needs_review`.

## 8. Production launch blockers

Items that must be resolved before the first real customer onboarding:

- **Terms jurisdiction.** Governing law is Texas, USA; venue is Travis County,
  Texas (`web/public/terms.html`).
- **Real-card billing verification.** The success-fee flow (card on file →
  20% charge on realized value → credit note on reversal) has been verified
  against mocked and test-mode Stripe only. Run one end-to-end charge and
  reversal against a real card in live mode before onboarding a paying customer.

## Team

- Architecture: James
- Security Features: Mark
- QA/QC Testing: Jamie
- Project Manager: Michael
