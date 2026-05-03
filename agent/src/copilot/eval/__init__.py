"""Eval framework — see EVAL.md.

Public surface:
    - ``Case`` and ``load_case`` for reading YAML eval cases
    - ``run_case`` for executing a case end-to-end against the agent
    - ``CaseResult`` for the structured per-case outcome

Pytest entry points live in ``agent/evals/test_*.py`` and parametrize over
the YAML files under ``agent/evals/<tier>/``.
"""

from __future__ import annotations

from .case import Case, CaseResult, DimensionResult, load_case, load_cases_in_dir
from .runner import run_case
from .scoreboard import render_scoreboard, tier_dimension_table

__all__ = [
    "Case",
    "CaseResult",
    "DimensionResult",
    "load_case",
    "load_cases_in_dir",
    "render_scoreboard",
    "run_case",
    "tier_dimension_table",
]
