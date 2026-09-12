"""RightsGraphService — builds and queries a per-tenant Rights Graph.

The graph is rebuilt on read from the same book records the reconciliation
engine consumes; it is never stored as its own collection and never produces
a dollar amount of its own.
"""
from __future__ import annotations

import logging

from ..book_loader import book_periods
from .adapter import B2BContractAdapter, DomainAdapter
from .models import (
    Discrepancy, EvaluationMode, ExpectedState, FinancialRight, Observation,
    RecoveryAction, RecoveryOutcome, RightsGraph, RightStatus,
)

logger = logging.getLogger(__name__)


class RightsGraphService:
    def __init__(self, account_id: str | None,
                 adapter: DomainAdapter | None = None):
        self.account_id = account_id
        self.adapter = adapter or B2BContractAdapter()

    # ---- build ------------------------------------------------------------

    def build_for_book(self, contracts, usage_list, invoices_list,
                       periods=None, mode: EvaluationMode = EvaluationMode.audit,
                       findings_by_id: dict[str, dict] | None = None,
                       compiled_rights=None, observations=None,
                       evaluations=None) -> RightsGraph:
        graph = RightsGraph()
        usage = {(u["customer_id"], u["period"]): u for u in usage_list}
        invoices = {(i["customer_id"], i["period"]): i for i in invoices_list}
        period_list = list(periods) if periods else book_periods(usage_list, invoices_list)

        for contract in contracts:
            cid = contract["customer_id"]
            evaluated = False
            for period in period_list:
                key = (cid, period)
                if key not in usage or key not in invoices:
                    continue
                evaluated = True
                findings, g = self.adapter.evaluate(
                    contract, usage[key], invoices[key], period,
                    self.account_id, mode=mode, needs_review=graph.needs_review)
                # Project recovery state from persisted finding lifecycle.
                for disc in g.discrepancies:
                    stored = dict((findings_by_id or {}).get(disc.finding_id) or {})
                    stored.setdefault("monthly_recoverable", disc.recoverable_amount)
                    actions, outcomes = self.adapter.build_recovery_context(
                        stored, disc.discrepancy_id)
                    g.recovery_actions.extend(actions)
                    g.outcomes.extend(outcomes)
                graph.merge(g)
            if not evaluated:
                # Customer has no billing data: still surface its rights'
                # gating status, and mark each active right not_evaluable for
                # every period — an observability gap, not a contract problem.
                _, _, rights, right_reviews = self.adapter.extract_rights(
                    contract, self.account_id)
                graph.needs_review.extend(right_reviews)
                for right in rights:
                    if right.status != RightStatus.active.value:
                        continue
                    for period in period_list:
                        graph.not_evaluable.append({
                            "right_id": right.right_id,
                            "right_type": right.right_type,
                            "customer_id": contract["customer_id"],
                            "period": period,
                            "missing_observation": "invoice_issued",
                            "reason": "right is valid but not evaluable for this "
                                      "period: no invoice_issued observation",
                        })
        if compiled_rights or observations or evaluations:
            from .adapter import project_novel_rights
            graph.merge(project_novel_rights(
                compiled_rights, observations, evaluations, self.account_id))
        self.assert_tenant(graph)
        return graph

    def build_for_customer(self, customer_id, contracts, usage_list, invoices_list,
                           periods=None, mode: EvaluationMode = EvaluationMode.audit,
                           findings_by_id: dict[str, dict] | None = None,
                           compiled_rights=None, observations=None,
                           evaluations=None):
        """Graph for one customer only; None if the customer isn't in the book
        and has no novel rights either."""
        mine = [c for c in contracts if c["customer_id"] == customer_id]
        novel_rights = [r for r in compiled_rights or []
                        if (r.get("customer_id") if isinstance(r, dict)
                            else getattr(r, "customer_id", None)) == customer_id]
        novel_obs = [o for o in observations or []
                     if o.get("customer_id") == customer_id]
        novel_evals = [e for e in evaluations or []]
        if not mine and not novel_rights:
            return None
        return self.build_for_book(mine, usage_list, invoices_list,
                                   periods=periods, mode=mode,
                                   findings_by_id=findings_by_id,
                                   compiled_rights=novel_rights,
                                   observations=novel_obs,
                                   evaluations=novel_evals)

    # ---- queries ----------------------------------------------------------

    def get_rights(self, graph: RightsGraph) -> list[FinancialRight]:
        return graph.rights

    def get_observations(self, graph: RightsGraph) -> list[Observation]:
        return graph.observations

    def get_discrepancies(self, graph: RightsGraph) -> list[Discrepancy]:
        return graph.discrepancies

    def get_expected_states(self, graph: RightsGraph) -> list[ExpectedState]:
        return graph.expected_states

    def get_recovery_actions(self, graph: RightsGraph) -> list[RecoveryAction]:
        return graph.recovery_actions

    def get_trace(self, graph: RightsGraph, discrepancy_id: str) -> dict | None:
        for d in graph.discrepancies:
            if d.discrepancy_id == discrepancy_id:
                return d.calculation_trace
        return None

    def record_outcome(self, graph: RightsGraph, outcome: RecoveryOutcome) -> None:
        """Append an outcome, deduping by id so re-recording is idempotent."""
        if all(o.outcome_id != outcome.outcome_id for o in graph.outcomes):
            graph.outcomes.append(outcome)

    # ---- tenant guard ------------------------------------------------------

    def assert_tenant(self, graph: RightsGraph) -> None:
        for entity in (*graph.sources, *graph.rights, *graph.observations):
            if entity.account_id != self.account_id:
                raise PermissionError(
                    f"rights graph entity {entity} belongs to account "
                    f"{entity.account_id!r}, not {self.account_id!r}")


def annotate_findings_with_graph(findings: list[dict], contracts, usage_list,
                                 invoices_list, period: str,
                                 account_id: str | None) -> list[dict]:
    """Pipeline hook: attach right_id/expected_state_id/discrepancy_id to
    already-computed findings WITHOUT re-running reconcile. Never raises —
    on any error the findings are returned unlinked."""
    try:
        adapter = B2BContractAdapter()
        usage = {(u["customer_id"], u["period"]): u for u in usage_list}
        invoices = {(i["customer_id"], i["period"]): i for i in invoices_list}
        by_customer = {}
        for f in findings:
            by_customer.setdefault(f["customer_id"], []).append(f)
        for contract in contracts:
            cid = contract["customer_id"]
            key = (cid, period)
            if cid in by_customer and key in usage and key in invoices:
                adapter.link_findings(contract, usage[key], invoices[key],
                                      period, account_id, by_customer[cid])
    except Exception:  # noqa: BLE001 - graph annotation must never break the pipeline
        logger.warning("rights graph annotation failed for period %s", period,
                       exc_info=True)
    return findings
