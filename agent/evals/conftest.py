"""Pytest fixtures for the eval tiers.

A single ``LangfuseClient`` is built per pytest session and shared across all
tiers; it auto-no-ops when Langfuse env vars are unset, so local runs work
without any setup.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Hydrate os.environ from agent/.env so module-level skip guards (which read
# os.environ before pytest fixtures run) see the same keys pydantic-settings
# would. ``override=False`` keeps real env vars dominant in CI.
_AGENT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_AGENT_ROOT / ".env", override=False)

# Evals are deterministic by design (README §"Eval tiers"): they run against
# the in-memory FIXTURE_BUNDLE so cases are reproducible across machines and
# don't depend on a live OpenEMR being reachable. Without this guard, a
# ``.env`` file that omits ``USE_FIXTURE_FHIR=1`` (the local default) silently
# routes the gate to the live API, every CareTeam search returns ``no_token``,
# and every smoke case fails with "I don't see this patient on your panel" —
# which historically masqueraded as agent regressions but was an env wiring
# bug. Set it here so eval invocations always pin fixture mode regardless of
# local config.
os.environ.setdefault("USE_FIXTURE_FHIR", "1")

from copilot.config import get_settings  # noqa: E402
from copilot.eval.case import Case, CaseResult, load_cases_in_dir  # noqa: E402
from copilot.eval.gates import evaluate_tier_gates, overall_exit_status  # noqa: E402
from copilot.eval.langfuse_client import LangfuseClient  # noqa: E402
from copilot.eval.scoreboard import render_scoreboard  # noqa: E402

EVALS_ROOT = Path(__file__).resolve().parent

# Per-session collector so the terminal summary can render the per-tier
# per-dimension scoreboard after every eval run. Populated by individual
# tier tests via ``record_case_result``.
_SESSION_RESULTS: list[CaseResult] = []


def record_case_result(result: CaseResult) -> None:
    """Stash a result so the session-end hook can render the scoreboard."""
    _SESSION_RESULTS.append(result)


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """Render the per-tier per-dimension pass-rate table after the run.

    Issue 017 — also prints a distinct ``[release-blocker]`` line per
    blocker case that failed so the failure mode is unmissable in the
    pytest summary.
    """
    if not _SESSION_RESULTS:
        return
    terminalreporter.write_sep("=", "eval scoreboard")
    terminalreporter.write_line(render_scoreboard(_SESSION_RESULTS))

    verdicts = evaluate_tier_gates(_SESSION_RESULTS)
    blocker_failures: list[str] = []
    for verdict in verdicts.values():
        blocker_failures.extend(verdict.details.get("blocker_failure_ids", []) or [])
    if blocker_failures:
        terminalreporter.write_sep("!", "release-blocker failures")
        for case_id in blocker_failures:
            terminalreporter.write_line(f"[release-blocker] {case_id}")


def pytest_sessionfinish(session, exitstatus) -> None:
    """Override pytest's exit status with the tier-differentiated gate verdict.

    Pytest's natural exit code is non-zero whenever any individual test
    failed. The gates intentionally tolerate sub-100% on golden (≥80%) and
    adversarial-quality (≥75%), so we re-derive the exit code from the
    gate verdicts. A run with no recorded results (e.g. import failure,
    everything skipped) falls through to pytest's own status.
    """
    if not _SESSION_RESULTS:
        return
    verdicts = evaluate_tier_gates(_SESSION_RESULTS)
    new_status = overall_exit_status(verdicts)
    # Don't paper over pytest's own collection / internal-error codes
    # (>= 2 in pytest's exit-code map). Only override the success/failure
    # ambiguity introduced by the gates.
    if exitstatus in (0, 1):
        session.exitstatus = new_status


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def langfuse(settings) -> Iterator[LangfuseClient]:
    client = LangfuseClient(settings)
    yield client
    client.flush()


def _load_tier(tier: str) -> list[Case]:
    directory = EVALS_ROOT / tier
    if not directory.exists():
        return []
    return load_cases_in_dir(directory)


def _id_for(case: Case) -> str:
    return case.id


@pytest.fixture
def smoke_cases() -> list[Case]:
    return _load_tier("smoke")


@pytest.fixture
def golden_cases() -> list[Case]:
    return _load_tier("golden")


@pytest.fixture
def adversarial_cases() -> list[Case]:
    return _load_tier("adversarial")


@pytest.fixture
def drift_cases() -> list[Case]:
    return _load_tier("drift")


# Parametrize collection — each test module imports the matching list.
def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    tier_to_arg = {
        "smoke_case": "smoke",
        "golden_case": "golden",
        "adversarial_case": "adversarial",
        "drift_case": "drift",
    }
    for arg_name, tier in tier_to_arg.items():
        if arg_name in metafunc.fixturenames:
            cases = _load_tier(tier)
            metafunc.parametrize(arg_name, cases, ids=[_id_for(c) for c in cases])
