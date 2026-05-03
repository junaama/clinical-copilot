"""Eval case schema + loader.

Cases live as YAML files under ``agent/evals/<tier>/``. The schema mirrors
EVAL.md §3.2 — anything we don't yet check is parsed and ignored, so cases
can carry forward-looking expectations safely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Case:
    """A single eval case loaded from YAML."""

    id: str
    tier: str
    description: str
    workflow: str
    path: Path

    # Persona + session
    user_id: str
    user_role: str
    care_team_includes: list[str]
    patient_id: str
    conversation_id: str | None
    prior_turns: list[dict[str, Any]]

    # Input
    message: str

    # Expected
    expected_workflow: str | None
    expected_decision: str
    classifier_confidence_min: float | None
    required_facts: list[str]
    required_citation_refs: list[str]
    forbidden_claims: list[str]
    forbidden_pids: list[str]
    citation_completeness_min: float
    latency_ms_max: int | None
    cost_usd_max: float | None

    # Adversarial extension (optional)
    attack: dict[str, Any] | None
    defense_required: list[str]

    # Raw YAML for forward-compatibility (so evaluators can read fields we
    # haven't promoted into the dataclass yet)
    raw: dict[str, Any] = field(repr=False)


@dataclass
class DimensionResult:
    """One scored dimension for a case (substring, citation, faithfulness, …).

    ``passed`` is the binary verdict that contributes to the case's overall
    AND-gate. ``score`` is an optional continuous metric (e.g. fraction of
    citations supported) for the scoreboard. ``details`` carries free-form
    diagnostic data that pytest failure messages and Langfuse can surface.
    """

    name: str
    passed: bool
    score: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class CaseResult:
    """Per-case outcome with all metrics scored."""

    case: Case
    passed: bool
    response_text: str
    citations: list[str]
    tool_calls: list[dict[str, Any]]
    decision: str
    latency_ms: int
    cost_usd: float
    prompt_tokens: int
    completion_tokens: int
    scores: dict[str, Any]
    failures: list[str]
    dimensions: dict[str, DimensionResult] = field(default_factory=dict)
    error: str | None = None
    trace_id: str | None = None

    def recompute_passed(self) -> None:
        """Set ``passed`` to (no error) AND (every dimension passed).

        Cases with no scored dimensions and no error pass — the assumption
        is that nothing has flagged a problem. The runner is responsible
        for populating dimensions before calling this.
        """
        if self.error is not None:
            self.passed = False
            return
        self.passed = all(d.passed for d in self.dimensions.values())

    def summary_line(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (
            f"{status:4s}  {self.case.id:40s}  "
            f"latency={self.latency_ms:5d}ms  "
            f"cost=${self.cost_usd:0.4f}  "
            f"tools={len(self.tool_calls)}  "
            f"cites={len(self.citations)}"
        )


def load_case(path: Path) -> Case:
    """Parse a YAML eval case into a typed ``Case``."""
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping")

    auth = raw.get("authenticated_as", {}) or {}
    session = raw.get("session_context", {}) or {}
    inputs = raw.get("input", {}) or {}
    expected = raw.get("expected", {}) or {}

    return Case(
        id=raw["id"],
        tier=raw.get("tier", _infer_tier_from_path(path)),
        description=raw.get("description", ""),
        workflow=raw.get("workflow", ""),
        path=path,
        user_id=auth.get("user_id", "unknown_user"),
        user_role=auth.get("role", "hospitalist"),
        care_team_includes=list(auth.get("care_team_includes", []) or []),
        patient_id=session.get("patient_id", ""),
        conversation_id=session.get("conversation_id"),
        prior_turns=list(session.get("prior_turns", []) or []),
        message=inputs.get("message", ""),
        expected_workflow=expected.get("workflow_id"),
        expected_decision=expected.get("decision", "allow"),
        classifier_confidence_min=expected.get("classifier_confidence_min"),
        required_facts=list(expected.get("required_facts", []) or []),
        required_citation_refs=list(expected.get("required_citation_refs", []) or []),
        forbidden_claims=list(expected.get("forbidden_claims", []) or []),
        forbidden_pids=[str(p) for p in (expected.get("forbidden_pids_in_response", []) or [])],
        citation_completeness_min=float(expected.get("citation_completeness_min", 1.0)),
        latency_ms_max=expected.get("latency_ms_max"),
        cost_usd_max=expected.get("cost_usd_max"),
        attack=raw.get("attack"),
        defense_required=list(raw.get("defense_required", []) or []),
        raw=raw,
    )


def load_cases_in_dir(directory: Path) -> list[Case]:
    """Load every ``.yaml`` file in ``directory`` (recursive)."""
    cases: list[Case] = []
    for path in sorted(directory.rglob("*.yaml")):
        if path.name.startswith("_"):
            continue  # _shared/ etc.
        cases.append(load_case(path))
    return cases


def _infer_tier_from_path(path: Path) -> str:
    """Best-effort tier inference: ``evals/golden/...`` → ``golden``."""
    for part in path.parts:
        if part in {"smoke", "golden", "adversarial", "drift"}:
            return part
    return "unknown"
