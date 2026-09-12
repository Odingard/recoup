"""AI-native financial-right discovery: LLM proposes and verifies candidate
rights; a deterministic compiler validates them against the RightSpec grammar
and a deterministic runtime evaluates them. The reconciliation engine remains
the sole calculator for legacy families — novel rights get their own closed,
non-LLM runtime.
"""
from __future__ import annotations

import importlib

from . import compiler, models, runtime  # deterministic modules, safe to import
from .compiler import compile_candidate_right, route_family
from .runtime import evaluate_right
from .models import (
    CandidateFinancialRight, CandidateStatus, CalculationSpec,
    CompileFailure, CompiledRight, ContractualConstant, EvaluationResult,
    RecoveryCase, RecoveryRecommendation, RightSpec, TriggerSpec,
)


def __getattr__(name):
    # AI modules import google.genai lazily inside functions; expose them here
    # without importing at package load so compiler/runtime stay LLM-free.
    if name in ("discovery", "verifier", "investigation", "strategist", "prompts"):
        return importlib.import_module(f".{name}", __name__)
    functions = {
        "discover_financial_rights": "discovery",
        "document_text_from_file": "discovery",
        "verify_candidate_right": "verifier",
        "build_recovery_case": "investigation",
        "recommend_recovery": "strategist",
    }
    if name in functions:
        return getattr(importlib.import_module(
            f".{functions[name]}", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CandidateFinancialRight", "CandidateStatus", "CalculationSpec",
    "CompileFailure", "CompiledRight", "ContractualConstant",
    "EvaluationResult", "RecoveryCase", "RecoveryRecommendation", "RightSpec",
    "TriggerSpec", "compiler", "models", "runtime",
]
