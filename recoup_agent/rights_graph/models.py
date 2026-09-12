"""Rights Graph canonical models — plain dataclasses, stdlib only.

Every dollar amount carried by these entities is COPIED from a
`reconciliation.reconcile()` finding or an invoice/usage field. No entity
here computes money; the reconciliation engine stays the only calculator.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any


class EvaluationMode(str, Enum):
    """audit: reconcile what already happened.

    preflight: accepted for forward-looking checks, but currently behaves
    identically to audit — there is no separate preflight enforcement yet.
    """

    audit = "audit"
    preflight = "preflight"


class RightStatus(str, Enum):
    active = "active"
    inactive = "inactive"


class ReviewStatus(str, Enum):
    confirmed = "confirmed"
    needs_review = "needs_review"


def _to_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_to_value(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_value(v) for k, v in value.items()}
    return value


@dataclass
class _Entity:
    def to_dict(self) -> dict:
        return {f.name: _to_value(getattr(self, f.name)) for f in fields(self)}


@dataclass
class AuthoritySource(_Entity):
    source_id: str
    account_id: str | None
    source_type: str
    external_reference: str | None = None
    counterparty_id: str | None = None
    effective_date: str | None = None
    expiration_date: str | None = None
    document_hash: str | None = None
    ingestion_timestamp: str | None = None
    status: str = "active"
    metadata: dict = field(default_factory=dict)


@dataclass
class EvidenceReference(_Entity):
    evidence_id: str
    source_id: str
    locator: str
    quoted_text: str = ""
    page: int | None = None
    section: str | None = None
    confidence: float | None = None
    extraction_method: str | None = None
    content_hash: str | None = None


@dataclass
class FinancialRight(_Entity):
    right_id: str
    account_id: str | None
    source_id: str
    holder_party_id: str | None
    obligor_party_id: str | None
    right_type: str
    description: str = ""
    effective_from: str | None = None
    effective_until: str | None = None
    trigger_definition: str = ""
    calculation_rule: str = ""
    calculation_inputs: dict = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    extraction_confidence: float | None = None
    review_status: str = ReviewStatus.needs_review.value
    status: str = RightStatus.inactive.value
    metadata: dict = field(default_factory=dict)


@dataclass
class Observation(_Entity):
    observation_id: str
    account_id: str | None
    observation_type: str
    party_id: str | None = None
    counterparty_id: str | None = None
    period: str | None = None
    occurred_at: str | None = None
    amount: float | None = None
    quantity: float | None = None
    value: float | None = None  # generic numeric reading (novel rights)
    unit: str | None = None
    source_system: str | None = None
    external_reference: str | None = None
    evidence: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


@dataclass
class ExpectedState(_Entity):
    expected_state_id: str
    right_id: str
    period: str | None = None
    expected_amount: float | None = None
    currency: str = "USD"
    deterministic_rule: str = ""
    calculation_trace: dict = field(default_factory=dict)
    input_observation_ids: list[str] = field(default_factory=list)
    generated_at: str | None = None


@dataclass
class Discrepancy(_Entity):
    discrepancy_id: str
    right_id: str
    expected_state_id: str | None = None
    actual_observation_ids: list[str] = field(default_factory=list)
    discrepancy_type: str = ""
    expected_amount: float | None = None
    actual_amount: float | None = None
    recoverable_amount: float | None = None
    calculation_trace: dict = field(default_factory=dict)
    confidence: float | None = None
    status: str = "open"
    finding_id: str | None = None  # bridge back to the legacy finding


@dataclass
class RecoveryAction(_Entity):
    action_id: str
    discrepancy_id: str
    action_type: str
    proposed_at: str | None = None
    approved_at: str | None = None
    executed_at: str | None = None
    status: str = "proposed"
    human_approval_required: bool = True
    external_reference: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class RecoveryOutcome(_Entity):
    outcome_id: str
    action_id: str
    discrepancy_id: str
    outcome_type: str
    amount_recovered: float | None = None
    attempted_at: str | None = None
    resolved_at: str | None = None
    resolution: str | None = None
    evidence: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    # Intelligence fields (additive; populated when outcome data is available)
    strategy_used: str | None = None
    counterparty_response: str | None = None
    accepted_without_dispute: bool | None = None
    dispute_reason: str | None = None
    amount_requested: float | None = None
    days_to_resolution: int | None = None
    evidence_strength: str | None = None
    right_family: str | None = None


_ENTITY_LISTS = (
    "sources", "evidence", "rights", "observations", "expected_states",
    "discrepancies", "recovery_actions", "outcomes",
)

_ID_FIELD = {
    "sources": "source_id",
    "evidence": "evidence_id",
    "rights": "right_id",
    "observations": "observation_id",
    "expected_states": "expected_state_id",
    "discrepancies": "discrepancy_id",
    "recovery_actions": "action_id",
    "outcomes": "outcome_id",
}


@dataclass
class RightsGraph(_Entity):
    sources: list[AuthoritySource] = field(default_factory=list)
    evidence: list[EvidenceReference] = field(default_factory=list)
    rights: list[FinancialRight] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    expected_states: list[ExpectedState] = field(default_factory=list)
    discrepancies: list[Discrepancy] = field(default_factory=list)
    recovery_actions: list[RecoveryAction] = field(default_factory=list)
    outcomes: list[RecoveryOutcome] = field(default_factory=list)
    needs_review: list[dict] = field(default_factory=list)
    # Active rights that cannot be evaluated for a period because the required
    # observation is absent — an observability gap, not a contractual problem.
    not_evaluable: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        out = {name: [e.to_dict() for e in getattr(self, name)] for name in _ENTITY_LISTS}
        out["needs_review"] = [dict(n) for n in self.needs_review]
        out["not_evaluable"] = [dict(n) for n in self.not_evaluable]
        return out

    def merge(self, other: "RightsGraph") -> "RightsGraph":
        """Union `other` into self, deduping by entity id — the idempotency
        guarantee: merging a graph with itself changes nothing."""
        for name in _ENTITY_LISTS:
            mine = getattr(self, name)
            seen = {getattr(e, _ID_FIELD[name]) for e in mine}
            for entity in getattr(other, name):
                if getattr(entity, _ID_FIELD[name]) not in seen:
                    mine.append(entity)
                    seen.add(getattr(entity, _ID_FIELD[name]))
        seen_nr = {repr(sorted(n.items())) for n in self.needs_review}
        for item in other.needs_review:
            if repr(sorted(item.items())) not in seen_nr:
                self.needs_review.append(item)
        seen_ne = {(n.get("right_id"), n.get("period")) for n in self.not_evaluable}
        for item in other.not_evaluable:
            key = (item.get("right_id"), item.get("period"))
            if key not in seen_ne:
                self.not_evaluable.append(item)
                seen_ne.add(key)
        return self
