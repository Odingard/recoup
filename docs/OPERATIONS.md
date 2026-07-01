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
| `GOOGLE_CLOUD_PROJECT` | GCP project for Firestore + Vertex AI |
| `GOOGLE_GENAI_USE_VERTEXAI` | `TRUE` to use Gemini via Vertex AI |
| `RECOUP_SAMPLE_MODE` | `1` = offline synthetic demo, no auth/Firestore. `0` in production |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Service-account JSON or path (omit on Cloud Run to use ADC) |
| `STRIPE_API_KEY` (or `STRIPE`) | **Read-only** key for the customer's Stripe account |
| `RECOUP_BILLING_SOURCE` | `stripe` to reconcile from Stripe instead of the synthetic/Firestore book |
| `RECOUP_BILLING_STRIPE_API_KEY` | **Separate** Stripe account used to invoice Recoup's success fee |
| `PORT` | Server port (Cloud Run injects this) |

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

## 3. Deploy to Cloud Run

```bash
gcloud run deploy recoup --source . \
  --region us-central1 \
  --set-env-vars GOOGLE_CLOUD_PROJECT=your-project,GOOGLE_GENAI_USE_VERTEXAI=TRUE
# Provide STRIPE_API_KEY, RECOUP_BILLING_STRIPE_API_KEY, etc. via --set-secrets from Secret Manager.
```

Store Stripe/Firebase secrets in **Secret Manager** and mount them with
`--set-secrets`; never bake them into the image.

## 4. Customer onboarding flow

1. **Sign in** with Firebase (Google). The account is isolated in Firestore under
   `accounts/{account_id}/...`.
2. **Upload contracts** — structured form now, or `POST /api/ingest/contract/document`
   with a text-based PDF/DOCX/TXT (Gemini extracts terms with confidence + provenance).
3. **Connect Stripe** — provide a read-only key server-side.
4. **Run reconciliation** for a billing period.
5. **Confirm extracted terms** — anything below 0.85 confidence is flagged.
6. **Review findings** — approve or reject each.
7. **Mark recovered** once the money is actually collected.
8. **Billing** — Recoup invoices 20% of *recovered* dollars via its own Stripe account.
   Export findings to CSV anytime.

## 5. Robustness

No bad input returns a 500. Corrupt files, scanned/image PDFs, unsupported formats,
empty Stripe accounts, and missing fields all return a clear, actionable message and
keep going. Low-confidence or unmappable data becomes `needs_review`.

## Team

- Architecture: James
- Security Features: Mark
- QA/QC Testing: Jamie
- Project Manager: Michael
