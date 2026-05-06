"""W2 fixture-based eval runner (issue 010).

Each W2 case YAML carries a pre-recorded ``fixture_response`` (the agent's
output text) and an optional ``fixture_extraction`` (the structured
extraction the upstream VLM would have produced). The runner scores the
five boolean rubrics against those fixtures — no live VLM, no agent
invocation, deterministic, fast (<30s for 50 cases).

When the live pipeline lands (issues 004-009), a future change can flip
the runner from fixture mode to live mode by replacing ``fixture_response``
with the actual ``run_case`` output. The rubric functions don't change.

Case YAML shape (minimal):

```yaml
id: w2-lab-001
category: lab_extraction
description: ...
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
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from .w2_evaluators import (
    RUBRIC_NAMES,
    RubricResult,
    aggregate_pass_rates,
    citation_present,
    factually_consistent,
    no_phi_in_logs,
    safe_refusal,
    schema_valid,
)

# ---------------------------------------------------------------------------
# Case shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class W2Case:
    """A fixture-based W2 eval case."""

    id: str
    category: str
    description: str
    path: Path

    fixture_response: str
    fixture_extraction: dict[str, Any] | None
    schema_name: str | None
    should_refuse: bool
    forbidden_pids: list[str]
    forbidden_names: list[str]

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
    """Parse a W2 fixture YAML into a ``W2Case``.

    Fails loudly on missing required fields so a malformed case can't
    silently skew the gate.
    """
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping")

    case_id = raw.get("id")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError(f"{path}: missing required 'id'")

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

    return W2Case(
        id=case_id,
        category=str(raw.get("category", "uncategorized")),
        description=str(raw.get("description", "")),
        path=path,
        fixture_response=fixture_response,
        fixture_extraction=fixture_extraction,
        schema_name=raw.get("schema") if isinstance(raw.get("schema"), str) else None,
        should_refuse=bool(raw.get("should_refuse", False)),
        forbidden_pids=[str(p) for p in (raw.get("forbidden_pids") or [])],
        forbidden_names=[str(n) for n in (raw.get("forbidden_names") or [])],
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


def score_w2_case(case: W2Case) -> W2CaseResult:
    """Run all five rubrics against a fixture case and aggregate.

    A case passes when every rubric's verdict matches the case's
    ``expected[<rubric>]``. ``expected[<rubric>] == True`` is the default
    (the rubric should pass). ``expected[<rubric>] == False`` means the
    case is a deliberate negative — the rubric is supposed to flag the
    response as failing — and the case itself passes when the rubric
    correctly fails.
    """
    rubrics: dict[str, RubricResult] = {}

    schema_cls = (
        _SCHEMA_REGISTRY.get(case.schema_name) if case.schema_name else None
    )
    rubrics["schema_valid"] = schema_valid(case.fixture_extraction, schema_cls)
    rubrics["citation_present"] = citation_present(case.fixture_response)
    rubrics["factually_consistent"] = factually_consistent(
        case.fixture_response, case.fixture_extraction
    )
    rubrics["safe_refusal"] = safe_refusal(case.fixture_response, case.should_refuse)
    rubrics["no_phi_in_logs"] = no_phi_in_logs(
        case.fixture_response,
        forbidden_pids=case.forbidden_pids,
        forbidden_names=case.forbidden_names,
    )

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
    return W2CaseResult(
        case=case,
        rubrics=rubrics,
        case_passed=case_passed,
        failures=failures,
    )


def score_w2_cases(cases: list[W2Case]) -> list[W2CaseResult]:
    return [score_w2_case(c) for c in cases]


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
