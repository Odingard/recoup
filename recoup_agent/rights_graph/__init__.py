"""Recoup Rights Graph: a deterministic, provenance-linked projection of
contractual financial rights and observed billing reality.

Reconciliation stays the only money calculator; this package links its
findings to the rights and evidence they rest on.
"""
from .adapter import B2BContractAdapter, DomainAdapter
from .ids import stable_id
from .models import (
    AuthoritySource, Discrepancy, EvaluationMode, EvidenceReference,
    ExpectedState, FinancialRight, Observation, RecoveryAction,
    RecoveryOutcome, RightsGraph, ReviewStatus, RightStatus,
)
from .service import RightsGraphService, annotate_findings_with_graph

__all__ = [
    "AuthoritySource", "B2BContractAdapter", "Discrepancy", "DomainAdapter",
    "EvaluationMode", "EvidenceReference", "ExpectedState", "FinancialRight",
    "Observation", "RecoveryAction", "RecoveryOutcome", "ReviewStatus",
    "RightsGraph", "RightsGraphService", "RightStatus",
    "annotate_findings_with_graph", "stable_id",
]
