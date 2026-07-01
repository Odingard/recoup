"""Outcome-based pricing: Recoup charges 20% of dollars actually RECOVERED.

The fee applies only to findings whose lifecycle has reached the ``recovered``
state (proposed/open -> approved -> recovered). Proposed and approved dollars are
never billed; only recovered dollars are.
"""
from __future__ import annotations

from datetime import datetime, timezone

SUCCESS_FEE_PCT = 0.20


def _month(ts: str | None) -> str | None:
    return ts[:7] if ts else None


def compute_metrics(findings: list[dict], *, now: datetime | None = None) -> dict:
    """Summarize recovered dollars and Recoup's success fee from a set of findings."""
    now = now or datetime.now(timezone.utc)
    current_month = now.strftime("%Y-%m")

    recovered = [f for f in findings if f.get("status") == "recovered"]
    active = [f for f in findings if f.get("status") != "rejected"]

    recovered_to_date = sum(float(f.get("monthly_recoverable", 0) or 0) for f in recovered)
    recovered_this_month = sum(
        float(f.get("monthly_recoverable", 0) or 0)
        for f in recovered
        if _month(f.get("recovered_at")) == current_month
    )
    potential = sum(float(f.get("monthly_recoverable", 0) or 0) for f in active)

    return {
        "success_fee_pct": SUCCESS_FEE_PCT,
        "current_month": current_month,
        "recovered_count": len(recovered),
        "recovered_to_date": round(recovered_to_date, 2),
        "recovered_this_month": round(recovered_this_month, 2),
        "success_fee_to_date": round(recovered_to_date * SUCCESS_FEE_PCT, 2),
        "success_fee_this_month": round(recovered_this_month * SUCCESS_FEE_PCT, 2),
        "potential_monthly_recoverable": round(potential, 2),
    }
