"""Eval framework — see EVAL.md.

Public surface:
    - ``Case`` and ``load_case`` for reading YAML eval cases
    - ``run_case`` for executing a case end-to-end against the agent
    - ``CaseResult`` for the structured per-case outcome

Pytest entry points live in ``agent/evals/test_*.py`` and parametrize over
the YAML files under ``agent/evals/<tier>/``.
"""

from __future__ import annotations

from .case import Case, CaseResult, load_case, load_cases_in_dir
from .runner import run_case

__all__ = [
    "Case",
    "CaseResult",
    "load_case",
    "load_cases_in_dir",
    "run_case",
]
