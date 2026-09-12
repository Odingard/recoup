"""Investigation step: LLM writes the defensible recovery case.

Amounts and the deterministic calculation are copied from the assembled
context AFTER the model responds — the model's echo of numbers is never
trusted.
"""
from __future__ import annotations

import json

from .discovery import DEFAULT_MODEL, _default_client, generate
from .models import RecoveryCase
from .prompts import INVESTIGATION_SYSTEM, InvestigationOutput


def build_recovery_case(discrepancy_ctx: dict, *, client=None,
                        model: str = DEFAULT_MODEL) -> RecoveryCase:
    """discrepancy_ctx (deterministically assembled): discrepancy_id,
    governing_authority, source_evidence, right_summary, trigger_trace,
    observed_facts, calculation_trace, amount, confidence."""
    client = client or _default_client()
    contents = f"<case_input>\n{json.dumps(discrepancy_ctx, default=str)}\n</case_input>"
    out: InvestigationOutput = generate(client, INVESTIGATION_SYSTEM, contents,
                                        InvestigationOutput, model)
    case = RecoveryCase(
        discrepancy_id=discrepancy_ctx["discrepancy_id"],
        governing_authority=out.governing_authority,
        source_evidence=discrepancy_ctx.get("source_evidence", ""),
        right_summary=out.right_summary,
        trigger_summary=out.trigger_summary,
        observed_facts=out.observed_facts,
        ambiguities=out.ambiguities,
        counter_evidence=out.counter_evidence,
        recommended_next_step=out.recommended_next_step,
        narrative=out.narrative,
        confidence=out.confidence or discrepancy_ctx.get("confidence"),
        model=model,
        # deterministic overwrite — never trust the model's echo:
        deterministic_calculation=discrepancy_ctx.get("calculation_trace", {}),
        amount=discrepancy_ctx.get("amount"),
    )
    return case
