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

```bash
adk deploy cloud_run \
  --project=$GOOGLE_CLOUD_PROJECT \
  --region=$GOOGLE_CLOUD_LOCATION \
  recoup_agent
```

The resulting public URL is your **Testing access** link for the submission.

## Notes

- **Data**: synthetic-but-realistic, modeled on common SaaS billing structures. No real customer data.
- **RAG**: `lookup_contract_clause` uses an in-record lookup for the demo; swap it for a Vertex AI Search query against a data store built from the contracts for production-grade RAG. (Marked in `tools.py`.)
- **Human-in-the-loop**: the action agent never calls `record_approval_decision` unless a human explicitly approves.

## Challenge

Google for Startups AI Agents Challenge — Track 1 (Build, Net-New Agents) · Region: AMERS.
