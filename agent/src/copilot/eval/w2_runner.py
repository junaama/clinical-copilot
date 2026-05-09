"""W2 eval runner.

Each W2 case YAML is scored against the five boolean rubrics
(``schema_valid``, ``citation_present``, ``factually_consistent``,
``safe_refusal``, ``no_phi_in_logs``). Two execution modes are supported:

* ``mode: validator_unit`` (default) — the case carries a static
  ``fixture_response`` string and the rubrics score it directly. Useful
  for unit-style positive/negative samples that exercise the validators
  themselves (e.g. malformed Pydantic payloads, deliberate uncited
  claims, PHI-leak probes against trace text). Deterministic, free,
  fast.

* ``mode: live`` — the case carries a real user ``prompt`` and the
  runner invokes the live agent (via ``copilot.eval.run_case``) to get
  its actual response, then scores the agent's output against the same
  rubrics. The rubric pass rates from this path are the real W2 quality
  gate.

The two modes share the rubric functions in
``copilot.eval.w2_evaluators`` — a regression in the validators trips
both tiers. They diverge only in where the response text and the
optional extraction payload come from.

Case YAML shape (minimal validator_unit case):

```yaml
id: w2-lab-001
category: lab_extraction
description: ...
mode: validator_unit       # default — may be omitted
fixture_response: |
  Total cholesterol 220 mg/dL <cite ref="DocumentReference/lab-001" value="220"/>.
fixture_extraction_path: fixtures/lab_001_extraction.json   # optional
schema: LabExtraction                                       # optional
should_refuse: false                                        # default false
forbidden_pids: []                                          # optional
expected:
  schema_valid: true       # set to false to mark "this case proves we catch X"
  citation_present: true
  factually_consistent: true
  safe_refusal: true       # treated as not_applicable when should_refuse=false
  no_phi_in_logs: true
```

Live case shape (additional fields):

```yaml
mode: live
prompt: "What does ADA recommend for A1C targets in adults with type 2 diabetes?"
patient_id: ""             # empty string for panel-spanning prompts
user_id: dr_lopez
care_team_includes: [fixture-1, fixture-2, fixture-3, fixture-4, fixture-5]
should_refuse: false
expected:
  schema_valid: true       # not_applicable for live cases without extractions
  citation_present: true
  factually_consistent: true
  safe_refusal: true
  no_phi_in_logs: true
```
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from . import llm_judge
from .w2_evaluators import (
    RUBRIC_NAMES,
    RubricResult,
    aggregate_pass_rates,
    citation_present,
    no_phi_in_logs,
    safe_refusal,
    schema_valid,
)
from .w2_evaluators import (
    factually_consistent as regex_factually_consistent,
)

_log = logging.getLogger(__name__)

# Operating modes for a W2 case. ``validator_unit`` scores the static
# ``fixture_response`` against the rubrics — useful for testing the
# validators themselves and for positive/negative samples whose live
# precondition (a specific DocumentReference fixture, a VLM extraction
# payload) doesn't exist in the agent's fixture data. ``live`` invokes
# the agent and scores its real response.
MODE_VALIDATOR_UNIT = "validator_unit"
MODE_LIVE = "live"
_SUPPORTED_MODES: frozenset[str] = frozenset({MODE_VALIDATOR_UNIT, MODE_LIVE})

_FALSE_ENV_VALUES = frozenset({"0", "false", "no", "off"})

# ---------------------------------------------------------------------------
# Case shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class W2Case:
    """A W2 eval case (validator_unit or live)."""

    id: str
    category: str
    description: str
    path: Path

    # Operating mode. Defaults to ``validator_unit``. Live cases also
    # populate ``prompt`` / ``patient_id`` / ``user_id`` /
    # ``care_team_includes``.
    mode: str

    fixture_response: str
    fixture_extraction: dict[str, Any] | None
    schema_name: str | None
    should_refuse: bool
    forbidden_pids: list[str]
    forbidden_names: list[str]

    # Live-mode only — when ``mode == "live"`` these drive the real agent
    # invocation. ``prompt`` is the user message; ``patient_id`` is the
    # active patient (empty string for panel-spanning prompts);
    # ``user_id`` + ``care_team_includes`` configure the CareTeam-gate so
    # the agent doesn't refuse the call before answering.
    prompt: str
    patient_id: str
    user_id: str
    care_team_includes: list[str]

    # Per-rubric expected verdicts. ``true`` means "this case should pass
    # the rubric"; ``false`` means "this case is a deliberate negative —
    # the rubric should detect the regression and fail." A case that lists
    # ``schema_valid: false`` plus a malformed fixture_extraction proves the
    # gate catches the problem; the runner's ``case_passed`` flips the flag
    # so the case as a whole is a positive (i.e. "regression detected").
    expected: dict[str, bool]


@dataclass
class W2CaseResult:
    """Per-case outcome with per-rubric scoring + overall verdict."""

    case: W2Case
    rubrics: dict[str, RubricResult]
    case_passed: bool
    failures: list[str] = field(default_factory=list)
    # When the case ran live, the actual response text from the agent.
    # Empty string for ``validator_unit`` cases (their response text is
    # the static ``fixture_response`` already on ``case``).
    live_response: str = ""
    # Tool calls observed during the live invocation, for debugging /
    # post-mortem in the gate report.
    live_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    live_error: str | None = None


# Map of schema name → Pydantic class. Filled in lazily so importing this
# module doesn't require issue 002's full schema set; cases requesting a
# schema that isn't registered fail their schema_valid rubric with a clear
# error rather than crashing.
_SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {}


def register_schema(name: str, cls: type[BaseModel]) -> None:
    """Make a Pydantic class addressable by name from W2 case YAML.

    Called from the test runner once at startup with whichever schemas are
    actually defined. Lazy registration keeps the W2 runner usable before
    issue 002 lands the full schema set.
    """
    _SCHEMA_REGISTRY[name] = cls


def registered_schemas() -> dict[str, type[BaseModel]]:
    return dict(_SCHEMA_REGISTRY)


# ---------------------------------------------------------------------------
# Case loading
# ---------------------------------------------------------------------------


def load_w2_case(path: Path) -> W2Case:
    """Parse a W2 case YAML into a ``W2Case``.

    Fails loudly on missing required fields so a malformed case can't
    silently skew the gate.
    """
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping")

    case_id = raw.get("id")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError(f"{path}: missing required 'id'")

    mode = raw.get("mode", MODE_VALIDATOR_UNIT)
    if not isinstance(mode, str) or mode not in _SUPPORTED_MODES:
        raise ValueError(
            f"{path}: 'mode' must be one of {sorted(_SUPPORTED_MODES)}; got {mode!r}"
        )

    fixture_response = raw.get("fixture_response", "")
    if not isinstance(fixture_response, str):
        raise ValueError(f"{path}: 'fixture_response' must be a string")

    fixture_extraction = None
    extraction_path = raw.get("fixture_extraction_path")
    inline_extraction = raw.get("fixture_extraction")
    if extraction_path is not None and inline_extraction is not None:
        raise ValueError(
            f"{path}: both 'fixture_extraction_path' and 'fixture_extraction' set; pick one"
        )
    if extraction_path is not None:
        ext_path = path.parent / extraction_path
        fixture_extraction = json.loads(ext_path.read_text())
    elif inline_extraction is not None:
        if not isinstance(inline_extraction, dict):
            raise ValueError(f"{path}: 'fixture_extraction' must be a mapping")
        fixture_extraction = inline_extraction

    expected_raw = raw.get("expected") or {}
    if not isinstance(expected_raw, dict):
        raise ValueError(f"{path}: 'expected' must be a mapping")
    expected = {name: bool(expected_raw.get(name, True)) for name in RUBRIC_NAMES}

    prompt = raw.get("prompt", "")
    if not isinstance(prompt, str):
        raise ValueError(f"{path}: 'prompt' must be a string")
    if mode == MODE_LIVE and not prompt:
        raise ValueError(
            f"{path}: 'mode: live' cases must include a non-empty 'prompt'"
        )

    patient_id = raw.get("patient_id", "")
    if patient_id is None:
        patient_id = ""
    if not isinstance(patient_id, str):
        raise ValueError(f"{path}: 'patient_id' must be a string (use '' for panel-wide)")

    user_id = raw.get("user_id", "")
    if not isinstance(user_id, str):
        raise ValueError(f"{path}: 'user_id' must be a string")

    care_team_includes = list(raw.get("care_team_includes") or [])
    care_team_includes = [str(p) for p in care_team_includes]

    return W2Case(
        id=case_id,
        category=str(raw.get("category", "uncategorized")),
        description=str(raw.get("description", "")),
        path=path,
        mode=mode,
        fixture_response=fixture_response,
        fixture_extraction=fixture_extraction,
        schema_name=raw.get("schema") if isinstance(raw.get("schema"), str) else None,
        should_refuse=bool(raw.get("should_refuse", False)),
        forbidden_pids=[str(p) for p in (raw.get("forbidden_pids") or [])],
        forbidden_names=[str(n) for n in (raw.get("forbidden_names") or [])],
        prompt=prompt,
        patient_id=patient_id,
        user_id=user_id,
        care_team_includes=care_team_includes,
        expected=expected,
    )


def load_w2_cases_in_dir(directory: Path) -> list[W2Case]:
    """Load every ``.yaml`` file under ``directory`` (recursive).

    Files prefixed with ``_`` are skipped (shared / fixture YAMLs).
    """
    cases: list[W2Case] = []
    for path in sorted(directory.rglob("*.yaml")):
        if path.name.startswith("_"):
            continue
        cases.append(load_w2_case(path))
    return cases


# ---------------------------------------------------------------------------
# Case scoring
# ---------------------------------------------------------------------------


def _score_response_against_rubrics(
    case: W2Case,
    response_text: str,
    fixture_extraction: dict[str, Any] | None,
) -> dict[str, RubricResult]:
    """Run all five rubrics against ``response_text`` + ``fixture_extraction``.

    Pure scoring helper shared by both validator_unit and live paths so
    a regression in either mode trips the same code. Live cases pass the
    agent's actual response and (typically) ``None`` for extraction —
    document-extraction cases that need a structured payload to score
    schema_valid stay as validator_unit.
    """
    rubrics: dict[str, RubricResult] = {}
    schema_cls = (
        _SCHEMA_REGISTRY.get(case.schema_name) if case.schema_name else None
    )
    rubrics["schema_valid"] = schema_valid(fixture_extraction, schema_cls)
    rubrics["citation_present"] = citation_present(response_text)
    rubrics["factually_consistent"] = _score_factually_consistent(
        case, response_text, fixture_extraction
    )
    rubrics["safe_refusal"] = safe_refusal(response_text, case.should_refuse)
    rubrics["no_phi_in_logs"] = no_phi_in_logs(
        response_text,
        forbidden_pids=case.forbidden_pids,
        forbidden_names=case.forbidden_names,
    )
    return rubrics


def llm_judge_enabled() -> bool:
    """Feature flag for the LLM-backed factual consistency judge."""
    raw = os.environ.get("EVAL_LLM_JUDGE_ENABLED", "true")
    return raw.strip().lower() not in _FALSE_ENV_VALUES


def _score_factually_consistent(
    case: W2Case,
    response_text: str,
    fixture_extraction: dict[str, Any] | None,
) -> RubricResult:
    if not llm_judge_enabled():
        return regex_factually_consistent(response_text, fixture_extraction)
    return llm_judge.factually_consistent(
        response_text,
        fixture_extraction,
        case_id=case.id,
    )


def _aggregate_case_verdict(
    case: W2Case, rubrics: dict[str, RubricResult]
) -> tuple[bool, list[str]]:
    """Compare per-rubric verdicts against the case's declared expectations.

    A case passes when every rubric's verdict matches
    ``case.expected[<rubric>]``. ``expected == True`` is "this rubric
    should pass" (positive sample); ``expected == False`` is "this case
    is a deliberate negative — the rubric should flag the regression and
    fail." A case passes either way when the actual verdict matches the
    declaration.
    """
    failures: list[str] = []
    case_passed = True
    for name in RUBRIC_NAMES:
        actual = rubrics[name].passed
        expected = case.expected[name]
        if actual is not expected:
            case_passed = False
            failures.append(
                f"{name}: expected pass={expected}, got pass={actual} — "
                f"details={rubrics[name].details}"
            )
    return case_passed, failures


def score_w2_case(case: W2Case) -> W2CaseResult:
    """Score a validator_unit case against the rubrics.

    The case's static ``fixture_response`` is the response text; its
    ``fixture_extraction`` (if any) feeds ``schema_valid`` and
    ``factually_consistent``. Live cases route through
    ``score_w2_case_live`` instead — calling ``score_w2_case`` on a live
    case scores the static fixture_response, which is the wrong target,
    so the runner refuses with a clear error.
    """
    if case.mode == MODE_LIVE:
        raise ValueError(
            f"{case.id}: cannot score a live case synchronously; "
            f"use score_w2_case_live or score_w2_cases_async"
        )
    rubrics = _score_response_against_rubrics(
        case, case.fixture_response, case.fixture_extraction
    )
    case_passed, failures = _aggregate_case_verdict(case, rubrics)
    return W2CaseResult(
        case=case,
        rubrics=rubrics,
        case_passed=case_passed,
        failures=failures,
    )


def score_w2_cases(cases: list[W2Case]) -> list[W2CaseResult]:
    """Score a list of validator_unit cases.

    Live cases must be scored via ``score_w2_cases_async`` — see that
    function's docstring for the live invocation contract.
    """
    return [score_w2_case(c) for c in cases]


# ---------------------------------------------------------------------------
# Live execution
# ---------------------------------------------------------------------------


async def score_w2_case_live(case: W2Case) -> W2CaseResult:
    """Invoke the live agent for a ``mode: live`` case and score the response.

    Builds a single-turn ``copilot.eval.case.Case`` from the W2 case's
    ``prompt`` / ``patient_id`` / ``user_id`` / ``care_team_includes``
    fields and runs it through the standard ``run_case`` machinery so
    the live W2 path exercises the same graph (classifier → agent →
    verifier) that smoke and golden tiers use. The agent's actual
    response text replaces ``fixture_response`` for rubric scoring.

    A runtime error during the agent invocation is recorded on the
    returned ``W2CaseResult`` (``live_error``) and treated as a hard
    fail for every rubric the case expected to pass — silent fail-open
    on a live error would let an outage masquerade as a clean run.
    """
    if case.mode != MODE_LIVE:
        raise ValueError(
            f"{case.id}: score_w2_case_live called on non-live case "
            f"(mode={case.mode!r})"
        )

    # Local imports to avoid pulling LangGraph + LLM dependencies on
    # every validator_unit run. The fixture-mode path (``score_w2_case``)
    # must stay importable in environments without ANTHROPIC_API_KEY.
    from .case import Case, Turn
    from .runner import run_case

    eval_case = Case(
        id=f"w2-live-{case.id}",
        tier="w2_live",
        description=case.description,
        workflow="W-2",
        path=case.path,
        user_id=case.user_id or "eval",
        user_role="hospitalist",
        care_team_includes=list(case.care_team_includes),
        patient_id=case.patient_id,
        conversation_id=None,
        prior_turns=[],
        turns=[Turn(prompt=case.prompt)],
        expected_workflow=None,
        expected_decision="allow",
        classifier_confidence_min=None,
        forbidden_claims=[],
        forbidden_pids=list(case.forbidden_pids),
        citation_completeness_min=0.0,
        latency_ms_max=None,
        cost_usd_max=None,
        attack=None,
        defense_required=[],
        raw={"id": case.id, "mode": "live"},
        release_blocker=False,
    )

    error: str | None = None
    response_text = ""
    tool_calls: list[dict[str, Any]] = []
    try:
        # ``run_case`` builds the full agent graph, exercises the verifier,
        # and returns a ``CaseResult`` with response text + tool calls. We
        # don't care about its dimension verdicts here — the W2 rubrics
        # are the gate; smoke/golden own the per-tier dimensions.
        case_result = await run_case(eval_case)
        response_text = case_result.response_text or ""
        tool_calls = list(case_result.tool_calls or [])
        if case_result.error:
            error = case_result.error
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        _log.exception("live W2 case %s failed", case.id)

    if error is not None:
        # Treat a live-runtime error as a hard fail across the board so
        # the case shows up in the per-rubric pass rate. Each rubric is
        # marked failed with the error in details so the report points
        # at the runtime issue rather than a phantom rubric verdict.
        rubrics = {
            name: RubricResult(
                name=name,
                passed=False,
                details={"error": error, "case_id": case.id},
            )
            for name in RUBRIC_NAMES
        }
        case_passed, failures = _aggregate_case_verdict(case, rubrics)
        # Prepend the runtime error so the gate report surfaces it first.
        failures.insert(0, f"runtime error during live invocation: {error}")
        return W2CaseResult(
            case=case,
            rubrics=rubrics,
            case_passed=case_passed,
            failures=failures,
            live_response="",
            live_tool_calls=tool_calls,
            live_error=error,
        )

    rubrics = _score_response_against_rubrics(
        case,
        response_text,
        # Live cases don't carry a fixture_extraction — schema_valid
        # auto-passes via not_applicable; factually_consistent does the
        # same when there's no extraction body.
        fixture_extraction=None,
    )
    case_passed, failures = _aggregate_case_verdict(case, rubrics)
    return W2CaseResult(
        case=case,
        rubrics=rubrics,
        case_passed=case_passed,
        failures=failures,
        live_response=response_text,
        live_tool_calls=tool_calls,
        live_error=None,
    )


async def score_w2_cases_async(
    cases: list[W2Case],
    *,
    live_concurrency: int = 4,
) -> list[W2CaseResult]:
    """Score a mixed list of validator_unit + live cases.

    Validator_unit cases are scored synchronously (no I/O). Live cases
    are dispatched concurrently with a semaphore to bound parallel LLM
    calls. Returns results in the same order as the input list so the
    gate can compare a regenerated baseline against the existing one.

    ``live_concurrency`` defaults to 4 — high enough to keep wall-clock
    short on a 10-20 case live tier, low enough that an Anthropic rate
    cap or a transient retrieval-stack hiccup doesn't take the whole
    run down at once.
    """
    semaphore = asyncio.Semaphore(live_concurrency)

    async def _run_one(case: W2Case) -> W2CaseResult:
        if case.mode == MODE_LIVE:
            async with semaphore:
                return await score_w2_case_live(case)
        return score_w2_case(case)

    return await asyncio.gather(*(_run_one(c) for c in cases))


def compute_pass_rates(results: list[W2CaseResult]) -> dict[str, float]:
    """Aggregate per-rubric pass rates across ``results``.

    A case marked ``expected[<rubric>] == False`` is excluded from that
    rubric's denominator — the case isn't a positive sample for that
    rubric, so counting it skews the rate. The remaining cases for each
    rubric give the realistic gate rate.
    """
    positive_per_case: list[dict[str, RubricResult]] = []
    for result in results:
        scored: dict[str, RubricResult] = {}
        for name in RUBRIC_NAMES:
            if result.case.expected.get(name, True):
                scored[name] = result.rubrics[name]
        positive_per_case.append(scored)
    return aggregate_pass_rates(positive_per_case)
