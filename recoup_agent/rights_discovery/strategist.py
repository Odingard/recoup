"""Strategist step: LLM recommends a recovery route from a fixed allowlist.

Recommendation only — nothing is executed, human approval is always required.
"""
from __future__ import annotations

import json

from .discovery import DEFAULT_MODEL, _default_client, generate
from .models import RecoveryCase, RecoveryRecommendation
from .prompts import STRATEGIES, STRATEGIST_SYSTEM, StrategistOutput


def recommend_recovery(case: RecoveryCase, *, client=None,
                       model: str = DEFAULT_MODEL) -> RecoveryRecommendation:
    client = client or _default_client()
    contents = f"<case>\n{json.dumps(case.to_dict(), default=str)}\n</case>"
    out: StrategistOutput = generate(client, STRATEGIST_SYSTEM, contents,
                                     StrategistOutput, model)
    strategy = out.strategy if out.strategy in STRATEGIES else "manual_review"
    return RecoveryRecommendation(
        discrepancy_id=case.discrepancy_id,
        strategy=strategy,
        rationale=out.rationale,
        draft_communication=out.draft_communication,
        requires_human_approval=True,
        model=model,
    )
