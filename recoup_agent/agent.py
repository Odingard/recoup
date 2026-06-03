"""Recoup: a multi-agent revenue-recovery pipeline built on ADK.

  ingestion -> reconciliation -> investigation -> action

Run locally:   adk web        (pick "recoup")   or   adk run recoup_agent
Deploy:        adk deploy cloud_run --project=$GOOGLE_CLOUD_PROJECT \
                 --region=$GOOGLE_CLOUD_LOCATION recoup_agent
"""
from google.adk.agents import LlmAgent, SequentialAgent

from .tools import (
    list_contracts, run_reconciliation, get_findings, lookup_contract_clause,
    draft_corrective_invoice, submit_for_approval, record_approval_decision,
)

MODEL = "gemini-2.5-flash"

ingestion_agent = LlmAgent(
    name="ingestion_agent",
    model=MODEL,
    description="Loads customer contracts and billing data and summarizes the terms.",
    instruction=(
        "You are the ingestion step of Recoup, a revenue-recovery system.\n"
        "Call `list_contracts` to load the customer book, then give a brief, factual "
        "summary of each customer's key billing terms (committed minimum, included units, "
        "overage rate, discounts, escalator). Do not look for billing errors yet."
    ),
    tools=[list_contracts],
    output_key="ingestion_summary",
)

reconciliation_agent = LlmAgent(
    name="reconciliation_agent",
    model=MODEL,
    description="Compares contractual entitlements against actual billing and flags revenue leakage.",
    instruction=(
        "You are the reconciliation step of Recoup.\n"
        "Call `run_reconciliation` exactly once. It returns the authoritative findings and the "
        "total monthly recoverable amount, computed deterministically.\n"
        "Report the total recoverable, then list each finding with its customer, type, and amount.\n"
        "CRITICAL: never invent or recompute numbers - use only the tool's output verbatim."
    ),
    tools=[run_reconciliation],
    output_key="reconciliation_summary",
)

investigation_agent = LlmAgent(
    name="investigation_agent",
    model=MODEL,
    description="Grounds each finding in the exact contract clause and writes a defensible justification.",
    instruction=(
        "You are the investigation step of Recoup.\n"
        "Call `get_findings` to retrieve the findings. For EACH finding, call "
        "`lookup_contract_clause` with its customer_id and clause_ref to fetch the governing "
        "contract language, then write a short, defensible justification grounded in that clause "
        "and stating the recoverable amount. Rank findings highest to lowest by amount."
    ),
    tools=[get_findings, lookup_contract_clause],
    output_key="investigation_report",
)

action_agent = LlmAgent(
    name="action_agent",
    model=MODEL,
    description="Drafts corrective invoices and routes them for human approval.",
    instruction=(
        "You are the action step of Recoup.\n"
        "1. Call `draft_corrective_invoice` for each customer that has findings.\n"
        "2. Call `submit_for_approval` to place every drafted item in the approval queue.\n"
        "3. Present the drafts to the human and clearly ask them to approve or reject each one.\n"
        "Never call `record_approval_decision` unless the human has explicitly approved or "
        "rejected a specific finding in their message. No money moves without human sign-off."
    ),
    tools=[draft_corrective_invoice, submit_for_approval, record_approval_decision],
    output_key="action_report",
)

root_agent = SequentialAgent(
    name="recoup",
    description="Recoup: finds the revenue you're already owed and recovers it.",
    sub_agents=[ingestion_agent, reconciliation_agent, investigation_agent, action_agent],
)
