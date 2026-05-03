"""Eval framework — see EVAL.md.

Public surface:
    - ``Case`` and ``load_case`` for reading YAML eval cases
    - ``run_case`` for executing a case end-to-end against the agent
    - ``CaseResult`` for the structured per-case outcome

Pytest entry points live in ``agent/evals/test_*.py`` and parametrize over
the YAML files under ``agent/evals/<tier>/``.
"""

from __future__ import annotations

from .case import Case, CaseResult, DimensionResult, Turn, load_case, load_cases_in_dir
from .faithfulness import (
    CitationClaim,
    CitationVerdict,
    FaithfulnessJudge,
    FaithfulnessResult,
    extract_citation_claims,
)
from .gates import (
    GATE_THRESHOLDS,
    GateVerdict,
    evaluate_tier_gates,
    overall_exit_status,
)
from .runner import run_case
from .scoreboard import render_scoreboard, tier_dimension_table
from .trajectory import TrajectoryResult, evaluate_trajectory

__all__ = [
    "GATE_THRESHOLDS",
    "Case",
    "CaseResult",
    "CitationClaim",
    "CitationVerdict",
    "DimensionResult",
    "FaithfulnessJudge",
    "FaithfulnessResult",
    "GateVerdict",
    "TrajectoryResult",
    "Turn",
    "evaluate_tier_gates",
    "evaluate_trajectory",
    "extract_citation_claims",
    "load_case",
    "load_cases_in_dir",
    "overall_exit_status",
    "render_scoreboard",
    "run_case",
    "tier_dimension_table",
]
