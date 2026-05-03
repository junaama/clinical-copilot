"""Trajectory dimension — set-membership over the agent's tool calls.

Issue 013 (eval suite v2). Given the list of tool-call records the
LangGraph agent produced and the list of ``required_tools`` declared in
the case YAML, return a structured result with missing required tools,
present-but-not-required tools, and a binary pass field.

Set-membership only — no ordering, no argument matching, no
forbidden-tools list. Cases with empty ``required_tools`` always pass
the dimension (no false negatives on cases that don't care about
trajectory).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .case import DimensionResult


@dataclass(frozen=True)
class TrajectoryResult:
    """Outcome of comparing observed tool calls against required tools."""

    passed: bool
    required_tools: list[str]
    missing: list[str]
    extra: list[str] = field(default_factory=list)

    def to_dimension_result(self) -> DimensionResult:
        """Project into a ``DimensionResult`` named ``trajectory``.

        Score is the fraction of required tools that were present
        (1.0 when no tools were required so the empty-required case
        doesn't drag the average down for scoreboard rollups).
        """
        if not self.required_tools:
            score = 1.0
        else:
            present = len(self.required_tools) - len(self.missing)
            score = present / len(self.required_tools)
        return DimensionResult(
            name="trajectory",
            passed=self.passed,
            score=score,
            details={
                "required": list(self.required_tools),
                "missing": list(self.missing),
                "extra": list(self.extra),
            },
        )


def evaluate_trajectory(
    tool_calls: Iterable[dict[str, Any]],
    required_tools: Iterable[str],
) -> TrajectoryResult:
    """Pure set-membership comparison of observed tool names to required.

    Tool-call records without a ``name`` key (malformed) are silently
    dropped; downstream callers should not need defensive checks for
    that shape.
    """
    required = list(required_tools)
    observed_names: set[str] = set()
    for call in tool_calls:
        name = call.get("name") if isinstance(call, dict) else None
        if isinstance(name, str) and name:
            observed_names.add(name)

    missing = [t for t in required if t not in observed_names]
    extra = sorted(observed_names - set(required))
    passed = not missing
    return TrajectoryResult(
        passed=passed,
        required_tools=required,
        missing=missing,
        extra=extra,
    )
