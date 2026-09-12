"""AI-native rights discovery: canonical models.

Discovery may be AI-assisted; compilation and evaluation are deterministic.
Nothing here executes AI-generated code — RightSpec compiles to a closed
grammar of allowlisted operators and calculation primitives only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any


class CandidateStatus(str, Enum):
    discovered = "discovered"
    verified = "verified"
    compiled = "compiled"
    legacy_routed = "legacy_routed"
    needs_review = "needs_review"
    unsupported = "unsupported"
    rejected = "rejected"


TRIGGER_OPERATORS = {
    "eq", "neq", "gt", "gte", "lt", "lte", "between",
    "date_reached", "date_before", "event_exists", "and", "or",
}

CALCULATION_PRIMITIVES = {
    "fixed_amount", "percentage_of", "per_unit", "difference", "tiered", "none",
}


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

    @classmethod
    def from_dict(cls, d: dict):
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


@dataclass
class TriggerSpec(_Entity):
    """Closed trigger grammar. Values are always references to named
    contractual constants ({"constant": name}) — never inline literals — so
    constant grounding stays enforceable."""
    op: str
    observation: str | None = None
    value: dict | None = None
    low: dict | None = None
    high: dict | None = None
    children: list[dict] = field(default_factory=list)


@dataclass
class CalculationSpec(_Entity):
    """Closed calculation grammar (see docs/RIGHTSPEC.md)."""
    type: str
    amount: dict | None = None
    rate: dict | None = None
    base_observation: str | None = None
    quantity_observation: str | None = None
    above: dict | None = None
    minuend: dict | None = None
    subtrahend: dict | None = None
    floor_zero: bool = True
    tiers: list[dict] = field(default_factory=list)


@dataclass
class ContractualConstant(_Entity):
    name: str
    value: Any  # float, or str for dates
    kind: str  # amount|percentage|rate|quantity|date|threshold
    currency: str | None = None
    evidence_ref: str | None = None


@dataclass
class RightSpec(_Entity):
    spec_version: str
    right_id: str
    right_family: str
    holder_party_id: str | None = None
    obligor_party_id: str | None = None
    trigger: dict | None = None
    calculation: dict | None = None
    required_observations: list[str] = field(default_factory=list)
    contractual_constants: list[dict] = field(default_factory=list)
    currency: str = "USD"
    effective_from: str | None = None
    effective_until: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    actual_observation: str | None = None
    # Only when the calculation is self-contained may a missing actual count
    # as 0 instead of not_evaluable.
    actual_observation_optional: bool = False


@dataclass
class CompiledRight(_Entity):
    spec: dict
    candidate_id: str
    compiler_version: str = "1.0"
    compiled_at: str | None = None
    discovery_model: str | None = None
    verification_model: str | None = None
    customer_id: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class CompileFailure(_Entity):
    candidate_id: str
    status: str  # CandidateStatus needs_review|unsupported|legacy_routed|rejected
    reasons: list[str] = field(default_factory=list)


@dataclass
class CandidateFinancialRight(_Entity):
    candidate_id: str
    account_id: str | None
    source_id: str
    holder_party_id: str | None
    obligor_party_id: str | None
    right_name: str
    right_family: str
    description: str = ""
    trigger_spec: dict | None = None
    calculation_spec: dict | None = None
    required_observations: list[str] = field(default_factory=list)
    effective_from: str | None = None
    effective_until: str | None = None
    evidence_refs: list[str] = field(default_factory=list)
    source_quote: str = ""
    discovery_confidence: float = 0.0
    verification_confidence: float | None = None
    discovery_model: str | None = None
    verification_model: str | None = None
    status: str = CandidateStatus.discovered.value
    rejection_reason: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class EvaluationResult(_Entity):
    right_id: str
    period: str | None = None
    triggered: bool | None = None
    expected_amount: float | None = None
    actual_amount: float | None = None
    recoverable_amount: float | None = None
    currency: str = "USD"
    input_observation_ids: list[str] = field(default_factory=list)
    calculation_trace: dict = field(default_factory=dict)
    evaluated_at: str | None = None
    status: str = "evaluated"  # evaluated|not_triggered|not_evaluable|error
    missing_observations: list[str] = field(default_factory=list)


@dataclass
class RecoveryCase(_Entity):
    discrepancy_id: str
    governing_authority: str = ""
    source_evidence: str = ""
    right_summary: str = ""
    trigger_summary: str = ""
    observed_facts: str = ""
    deterministic_calculation: dict = field(default_factory=dict)
    amount: float | None = None
    confidence: float | None = None
    ambiguities: list[str] = field(default_factory=list)
    counter_evidence: list[str] = field(default_factory=list)
    recommended_next_step: str = ""
    narrative: str = ""
    model: str | None = None


@dataclass
class RecoveryRecommendation(_Entity):
    discrepancy_id: str
    strategy: str
    rationale: str = ""
    draft_communication: str = ""
    requires_human_approval: bool = True  # constant: never executed by AI
    model: str | None = None
