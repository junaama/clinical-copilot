"""Smoke tests — graph compiles and tools wire up without an LLM call."""

from __future__ import annotations

import os

import pytest

from copilot.config import Settings
from copilot.fhir import FhirClient
from copilot.fixtures import PATIENT_ID
from copilot.tools import make_tools


def _settings() -> Settings:
    return Settings(LLM_PROVIDER="openai", OPENAI_API_KEY="test-key", USE_FIXTURE_FHIR=True)


def test_tools_built_for_uc2() -> None:
    tools = make_tools(_settings())
    names = {t.name for t in tools}
    assert {
        "get_patient_demographics",
        "get_active_problems",
        "get_active_medications",
        "get_recent_vitals",
        "get_recent_labs",
        "get_recent_encounters",
        "get_clinical_notes",
    } <= names


async def test_fixture_fhir_returns_patient() -> None:
    client = FhirClient(_settings())
    ok, resource, err, _ = await client.read("Patient", PATIENT_ID)
    assert ok
    assert resource is not None
    assert resource["name"][0]["family"] == "Perez"
    assert err is None


async def test_fixture_search_filters_by_patient() -> None:
    client = FhirClient(_settings())
    ok, entries, _, _ = await client.search(
        "Observation", {"patient": PATIENT_ID, "category": "vital-signs"}
    )
    assert ok
    assert all(
        e["subject"]["reference"] == f"Patient/{PATIENT_ID}" for e in entries
    )
    assert any(
        "90/60" in (e.get("valueString") or "") for e in entries
    ), "fixture should include the hypotensive event"


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"), reason="requires OPENAI_API_KEY"
)
async def test_graph_compiles_with_real_llm() -> None:
    from copilot.graph import build_graph

    graph = build_graph(_settings_with_real_key())
    assert graph is not None


def _settings_with_real_key() -> Settings:
    return Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY=os.environ["OPENAI_API_KEY"],
        USE_FIXTURE_FHIR=True,
    )
