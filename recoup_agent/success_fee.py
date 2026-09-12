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


def compute_metrics(findings: list[dict], events: list[dict] | None = None,
                    *, now: datetime | None = None) -> dict:
    """Summarize recovered dollars and Recoup's success fee from a set of
    findings. When ``events`` (recovery realization events) are given,
    recovered dollars and fees are computed per finding from the net of those
    events instead of the finding's ``recovered_amount`` field."""
    now = now or datetime.now(timezone.utc)
    current_month = now.strftime("%Y-%m")

    recovered = [f for f in findings if f.get("status") == "recovered"]
    active = [f for f in findings if f.get("status") not in {"rejected", "written_off"}]
    awaiting_payment = [f for f in findings if f.get("status") in {"invoiced", "disputed"}]
    written_off = [f for f in findings if f.get("status") == "written_off"]

    events_by_finding: dict[str, list[dict]] = {}
    by_basis: dict[str, float] = {}
    if events is not None:
        for e in events:
            events_by_finding.setdefault(e.get("finding_id"), []).append(e)
            basis = e.get("recovery_basis", "other_verified_value")
            if e.get("event_type") == "reversal":
                by_basis[basis] = by_basis.get(basis, 0.0) - (e.get("reversal_amount") or 0)
            else:
                by_basis[basis] = by_basis.get(basis, 0.0) + (e.get("realized_value") or 0)

    def _recovered_dollars(f: dict) -> float:
        if events is not None and f["finding_id"] in events_by_finding:
            return round(sum(
                -(e.get("reversal_amount") or 0) if e.get("event_type") == "reversal"
                else (e.get("realized_value") or 0)
                for e in events_by_finding[f["finding_id"]]), 2)
        return float(f.get("recovered_amount") or f.get("monthly_recoverable", 0) or 0)

    recovered_to_date = sum(_recovered_dollars(f) for f in recovered)
    recovered_this_month = sum(
        _recovered_dollars(f)
        for f in recovered
        if _month(f.get("recovered_at")) == current_month
    )
    potential = sum(float(f.get("monthly_recoverable", 0) or 0) for f in active)

    out = {
        "success_fee_pct": SUCCESS_FEE_PCT,
        "current_month": current_month,
        "recovered_count": len(recovered),
        "recovered_to_date": round(recovered_to_date, 2),
        "recovered_this_month": round(recovered_this_month, 2),
        "success_fee_to_date": round(recovered_to_date * SUCCESS_FEE_PCT, 2),
        "success_fee_this_month": round(recovered_this_month * SUCCESS_FEE_PCT, 2),
        "potential_monthly_recoverable": round(potential, 2),
        "invoiced_awaiting_payment": round(
            sum(float(f.get("monthly_recoverable", 0) or 0) for f in awaiting_payment), 2),
        "written_off": round(
            sum(float(f.get("monthly_recoverable", 0) or 0) for f in written_off), 2),
    }
    if events is not None:
        out["realized_value_by_basis"] = {
            k: round(v, 2) for k, v in sorted(by_basis.items())}
    return out
