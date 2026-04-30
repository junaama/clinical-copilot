"""Patient-context-mismatch hard block (ARCHITECTURE.md §7).

Even if the LLM tries to smuggle a different patient_id into a tool call, the
tool layer must refuse before issuing any FHIR query. These tests assert that
contract independent of the LLM.
"""

from __future__ import annotations

import pytest

from copilot.config import Settings
from copilot.tools import make_tools, set_active_patient_id


def _settings() -> Settings:
    return Settings(LLM_PROVIDER="openai", OPENAI_API_KEY="test", USE_FIXTURE_FHIR=True)


@pytest.fixture(autouse=True)
def _reset_context():
    set_active_patient_id(None)
    yield
    set_active_patient_id(None)


def _tool_by_name(name: str):
    for tool in make_tools(_settings()):
        if tool.name == name:
            return tool
    raise KeyError(name)


async def test_tool_rejects_mismatched_patient_id() -> None:
    set_active_patient_id("fixture-1")
    tool = _tool_by_name("get_active_medications")

    result = await tool.ainvoke({"patient_id": "intruder-2"})

    assert result["ok"] is False
    assert result["error"] == "patient_context_mismatch"
    assert result["rows"] == []


async def test_tool_allows_matching_patient_id() -> None:
    set_active_patient_id("fixture-1")
    tool = _tool_by_name("get_active_medications")

    result = await tool.ainvoke({"patient_id": "fixture-1"})

    assert result["ok"] is True
    assert any(r["fhir_ref"].startswith("MedicationRequest/") for r in result["rows"])


async def test_tool_allows_when_no_context_bound() -> None:
    """Unit tests / scripts that don't bind a context still work."""
    set_active_patient_id(None)
    tool = _tool_by_name("get_patient_demographics")

    result = await tool.ainvoke({"patient_id": "fixture-1"})

    assert result["ok"] is True


async def test_all_patient_scoped_tools_enforce_context() -> None:
    """Every tool that accepts a ``patient_id`` parameter must enforce the
    active SMART context. Tools that span the panel (UC-1 triage primitives
    like ``get_my_patient_list``) are intentionally exempt.
    """
    set_active_patient_id("fixture-1")
    tools = make_tools(_settings())
    for tool in tools:
        properties = tool.args_schema.model_json_schema().get("properties", {})
        if "patient_id" not in properties:
            # Panel-scoped tool — skipped per docstring.
            continue
        kwargs = {"patient_id": "intruder-2"}
        if "hours" in properties:
            kwargs["hours"] = 24
        result = await tool.ainvoke(kwargs)
        assert result["ok"] is False, f"{tool.name} did not enforce patient context"
        assert result["error"] == "patient_context_mismatch", tool.name
