"""Eval case schema + loader.

Cases live as YAML files under ``agent/evals/<tier>/``. The schema is
unified around a ``turns: [...]`` list (issue 014): single-turn cases are
a one-element list, multi-turn cases (issue 015) extend the list.

Each turn carries its own prompt, expected substrings (``must_contain``),
expected citations (``must_cite``), and trajectory ``required_tools``.
Case-wide gates (decision, forbidden claims, latency, cost, completeness)
stay at the top level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Turn:
    """One turn in an eval case.

    Single-turn cases have one ``Turn``; multi-turn cases (issue 015) have
    several. The runner scores every applicable dimension on every turn
    and AND-gates across them to produce the case verdict.
    """

    prompt: str
    must_contain: list[str] = field(default_factory=list)
    must_cite: list[str] = field(default_factory=list)
    required_tools: list[str] = field(default_factory=list)


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

    # Per-turn payload (issue 014). Single-turn cases have len(turns) == 1;
    # multi-turn (issue 015) extend this list. The runner reads the turn(s)
    # rather than the legacy single-prompt fields.
    turns: list[Turn]

    # Case-wide expected fields (apply across every turn)
    expected_workflow: str | None
    expected_decision: str
    classifier_confidence_min: float | None
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

    # ---- Per-turn projections for single-turn callers --------------------
    # The runner, evaluators, and Langfuse sync historically read flat
    # ``case.message`` / ``case.required_facts`` / ``case.required_citation_refs``
    # / ``case.required_tools`` fields. After the issue-014 migration these
    # are projections of ``turns[0]`` so the single-turn code path stays
    # one line of access. Slice 015's multi-turn runner iterates ``turns``
    # directly and won't touch these.

    @property
    def message(self) -> str:
        return self.turns[0].prompt if self.turns else ""

    @property
    def required_facts(self) -> list[str]:
        return self.turns[0].must_contain if self.turns else []

    @property
    def required_citation_refs(self) -> list[str]:
        return self.turns[0].must_cite if self.turns else []

    @property
    def required_tools(self) -> list[str]:
        return self.turns[0].required_tools if self.turns else []


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
    expected = raw.get("expected", {}) or {}

    turns = _parse_turns(raw, path)

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
        turns=turns,
        expected_workflow=expected.get("workflow_id"),
        expected_decision=expected.get("decision", "allow"),
        classifier_confidence_min=expected.get("classifier_confidence_min"),
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


_LEGACY_MIGRATION_HINT = (
    "Legacy single-prompt shape detected (top-level 'input.message' / "
    "'expected.required_facts' / 'expected.required_citation_refs' / "
    "'expected.required_tools'). Issue 014 unified the schema around a "
    "'turns: [...]' list. Rewrite as:\n"
    "  turns:\n"
    "    - prompt: <message>\n"
    "      must_contain: [<facts>]\n"
    "      must_cite: [<refs>]\n"
    "      trajectory:\n"
    "        required_tools: [<tools>]\n"
    "and remove the legacy keys from 'input' and 'expected'."
)


def _parse_turns(raw: dict[str, Any], path: Path) -> list[Turn]:
    """Parse the unified ``turns: [...]`` block.

    Rejects the legacy single-prompt shape with a clear migration hint so
    contributors don't accidentally land cases in the old format after the
    issue-014 migration.
    """
    turns_raw = raw.get("turns")
    expected = raw.get("expected") or {}
    inputs = raw.get("input") or {}

    legacy_keys_present = (
        "message" in inputs
        or "required_facts" in expected
        or "required_citation_refs" in expected
        or "required_tools" in expected
        or "trajectory" in expected
    )

    if turns_raw is None:
        raise ValueError(
            f"{path}: missing 'turns:' block. " + _LEGACY_MIGRATION_HINT
        )

    if legacy_keys_present:
        raise ValueError(
            f"{path}: 'turns:' block coexists with legacy fields. "
            + _LEGACY_MIGRATION_HINT
        )

    if not isinstance(turns_raw, list) or not turns_raw:
        raise ValueError(
            f"{path}: 'turns:' must be a non-empty list."
        )

    turns: list[Turn] = []
    for index, item in enumerate(turns_raw):
        if not isinstance(item, dict):
            raise ValueError(
                f"{path}: turns[{index}] must be a mapping, got {type(item).__name__}"
            )
        prompt = item.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError(
                f"{path}: turns[{index}].prompt must be a non-empty string"
            )
        trajectory = item.get("trajectory") or {}
        if not isinstance(trajectory, dict):
            raise ValueError(
                f"{path}: turns[{index}].trajectory must be a mapping"
            )
        turns.append(
            Turn(
                prompt=prompt,
                must_contain=[str(s) for s in (item.get("must_contain") or [])],
                must_cite=[str(s) for s in (item.get("must_cite") or [])],
                required_tools=[str(s) for s in (trajectory.get("required_tools") or [])],
            )
        )
    return turns


def _infer_tier_from_path(path: Path) -> str:
    """Best-effort tier inference: ``evals/golden/...`` → ``golden``."""
    for part in path.parts:
        if part in {"smoke", "golden", "adversarial", "drift"}:
            return part
    return "unknown"
