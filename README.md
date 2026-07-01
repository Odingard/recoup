# Recoup

**The revenue you're already owed.** An autonomous, multi-agent system that continuously reconciles what a B2B company *should* be billing against what it *is* billing — and recovers the gap.

![Recoup architecture](architecture.png)

## Problem

B2B companies systematically under-bill their own customers. Negotiated contract minimums go unenforced, usage above committed tiers never gets charged, promotional discounts outlive their expiry dates, and annual price escalators written into multi-year deals are forgotten at renewal. This revenue leakage quietly drains an estimated 1–5% of annual recurring revenue — money already earned and contractually owed — for one reason: no human cross-checks every contract against every invoice every month.

## Solution

A four-agent pipeline orchestrated with the Agent Development Kit (ADK):

1. **Ingestion agent** — loads contracts and billing data and summarizes each customer's terms.
2. **Reconciliation agent** — compares contractual entitlements against actual charges and flags every discrepancy.
3. **Investigation agent** — grounds each finding in the exact contract clause, justifies it, and ranks by recoverable value.
4. **Action agent** — drafts the corrective invoice and routes it for one-click human approval, with a full audit trail.

## How it works (the design that makes it trustworthy)

Money math is **deterministic Python** (`reconciliation.py`) — every dollar figure is computed, never guessed by an LLM. Gemini handles only what it's good at: grounding findings in contract language, writing defensible justifications, and drafting customer-facing memos. Structured findings flow between agents through ADK session state; the agents narrate. Nothing finalizes an invoice without an explicit human approval, and every decision is written to an audit log.

On the included synthetic book, Recoup surfaces **$14,200/mo ($170,400/yr) of recoverable revenue for Acme Corp** across three findings, correctly flags **Initech** for a missed escalator, and correctly leaves **Globex** alone.

## Phase 1 (Wedge) — production-ready demo

The Phase 1 build turns the demo into something a rep can put in front of a customer:

- **Real contract extraction** with confidence gating — Gemini extracts terms with a confidence score and provenance; anything below 0.85 or missing required input is flagged `needs_review` instead of producing a number.
- **Read-only Stripe connector** — reconcile against live customers, subscriptions, invoices, metered usage, and discounts. Unmappable data becomes `needs_review`, never a silent assumption.
- **Firebase auth + multi-tenancy** — every account is isolated in Firestore under `accounts/{account_id}/...`. No hardcoded project id or mock token.
- **Outcome-based pricing** — Recoup bills **20% of dollars actually recovered** (proposed → approved → recovered), invoiced through Recoup's own **separate** Stripe account.
- **Robust by default** — corrupt/scanned/unsupported files, empty Stripe accounts, and missing fields are flagged with actionable messages; no ingestion path returns a 500.
- **Sample mode** — `RECOUP_SAMPLE_MODE=1` runs the whole thing offline on the synthetic book, no credentials required.

See **[docs/OPERATIONS.md](docs/OPERATIONS.md)** for the operator guide (env vars, Cloud Run deploy, onboarding flow) and **[docs/DATA_HANDLING.md](docs/DATA_HANDLING.md)** for the plain-language data-handling statement. All configuration is via environment variables (`recoup_agent/.env.example`).

## Tech stack

- **Gemini 2.5 Flash** (via Vertex AI) — reasoning, clause grounding, drafting
- **Agent Development Kit (ADK)** — multi-agent orchestration (`SequentialAgent`)
- **Vertex AI Search** — RAG over the contract corpus (clause grounding; see note below)
- **Model Context Protocol (MCP)** — connect billing/CRM systems (production path)
- **Cloud Run** — deployment
- Designed for an **Agent-to-Agent (A2A)** layer as the enterprise path

## Repo structure

```
recoup/
  recoup_agent/
    agent.py            # the four agents + SequentialAgent root_agent
    tools.py            # ADK function tools (state-backed)
    reconciliation.py   # deterministic leakage rules (no LLM)
    pipeline.py         # engine + standalone deterministic demo
    synthetic_data.py   # generates the synthetic customer book
    data/               # contracts.json, usage.json, invoices.json
    .env.example
  test_reconciliation.py
  requirements.txt
  Dockerfile            # optional
```

## Quickstart

```bash
# 1. install
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. (re)generate the synthetic data
python -m recoup_agent.synthetic_data

# 3. run the deterministic demo — no credentials needed, always works
python -m recoup_agent.pipeline

# 4. configure Vertex AI for the agents
cp recoup_agent/.env.example recoup_agent/.env   # then edit your project id

# 5. run the multi-agent system
adk web        # opens the dev UI; pick "recoup"
# or
adk run recoup_agent
```

In the dev UI, send `Run the recovery pipeline`. The agents will ingest, reconcile, investigate, and draft corrective invoices, then **stop and ask for your approval**. Reply `approve F-ACME-001` (etc.) to record a decision to the audit log.

## Deploy to Cloud Run

Copy-paste; set the two variables at the top.

```bash
export PROJECT_ID="your-gcp-project-id"
export REGION="us-central1"

# one-time: select project + enable APIs
gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com aiplatform.googleapis.com artifactregistry.googleapis.com

# build the container, deploy to Cloud Run, and include the dev UI
adk deploy cloud_run \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --service_name=recoup \
  --with_ui \
  recoup_agent
# when prompted "Allow unauthenticated invocations?" answer: y

# set the Vertex env vars on the deployed service
gcloud run services update recoup --region="$REGION" \
  --set-env-vars=GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT="$PROJECT_ID",GOOGLE_CLOUD_LOCATION="$REGION"

# grant the Cloud Run service account access to Vertex AI (and Search, if using RAG)
export SA="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" --role="roles/aiplatform.user"
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$SA" --role="roles/discoveryengine.viewer"

# print your public URL -> this is your "Testing access" submission link
gcloud run services describe recoup --region="$REGION" --format='value(status.url)'
```

If you enabled the RAG upgrade below, also append
`,VERTEX_AI_SEARCH_ENGINE_ID=<your-engine-id>,VERTEX_AI_SEARCH_LOCATION=global` to the `--set-env-vars`.

## Vertex AI Search RAG upgrade (optional)

By default, clause grounding uses a local lookup. To run it as true RAG over a
contract corpus with Vertex AI Search, set it up once - the code switches over
automatically when `VERTEX_AI_SEARCH_ENGINE_ID` is set, and falls back to local
if it is not, so this can never break the demo.

```bash
export PROJECT_ID="your-gcp-project-id"

# 1. generate the corpus (28 contracts -> recoup_agent/data/corpus/*.txt)
python -m recoup_agent.synthetic_data

# 2. upload the corpus to a Cloud Storage bucket
export BUCKET="gs://${PROJECT_ID}-recoup-contracts"
gcloud storage buckets create "$BUCKET" --location=us
gcloud storage cp recoup_agent/data/corpus/*.txt "$BUCKET/"
```

3. Create a Vertex AI Search app (Console is easiest): **AI Applications -> Apps -> Create -> Search (Generic)**. For the data store, choose **Cloud Storage**, point it at the bucket folder, and select **Unstructured documents**. Note the **App / Engine ID** it creates.

4. Point Recoup at it - locally add `VERTEX_AI_SEARCH_ENGINE_ID` (and optionally `VERTEX_AI_SEARCH_LOCATION`) to `recoup_agent/.env`; on Cloud Run add them to the `--set-env-vars` above.

The `investigation_agent` will then ground each finding in clauses retrieved from Vertex AI Search instead of the local record. Implemented in `vertex_search.py`; wired in `tools.py`.

## Notes

- **Data**: synthetic-but-realistic, modeled on common SaaS billing structures. No real customer data.
- **RAG**: `lookup_contract_clause` uses an in-record lookup for the demo; swap it for a Vertex AI Search query against a data store built from the contracts for production-grade RAG. (Marked in `tools.py`.)
- **Human-in-the-loop**: the action agent never calls `record_approval_decision` unless a human explicitly approves.

## Challenge

Google for Startups AI Agents Challenge — Track 1 (Build, Net-New Agents) · Region: AMERS.
