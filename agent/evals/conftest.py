"""Pytest fixtures for the eval tiers.

A single ``LangfuseClient`` is built per pytest session and shared across all
tiers; it auto-no-ops when Langfuse env vars are unset, so local runs work
without any setup.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Hydrate os.environ from agent/.env so module-level skip guards (which read
# os.environ before pytest fixtures run) see the same keys pydantic-settings
# would. ``override=False`` keeps real env vars dominant in CI.
_AGENT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(_AGENT_ROOT / ".env", override=False)

from copilot.config import get_settings  # noqa: E402
from copilot.eval.case import Case, load_cases_in_dir  # noqa: E402
from copilot.eval.langfuse_client import LangfuseClient  # noqa: E402

EVALS_ROOT = Path(__file__).resolve().parent


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
