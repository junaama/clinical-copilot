"""Tool-layer authorization gate (issue 002, ARCHITECTURE.md §7).

Two coexisting paths:

1. Production / SMART-launched: ``user_id`` is bound on the contextvar by
   ``graph.agent_node``; the tool delegates to ``CareTeamGate`` and returns
   ``careteam_denied`` for patients off the user's team.

2. Isolated / dev: no ``user_id`` is bound; the tool falls back to the
   legacy SMART-pin check and returns ``patient_context_mismatch`` when a
   bound active patient doesn't equal the call's ``patient_id``. Bypasses
   the gate entirely so unit tests don't need a CareTeam fixture.

The contract test at the bottom of the file walks every patient-scoped
tool to ensure neither path was forgotten.
"""

from __future__ import annotations

import pytest

from copilot.config import Settings
from copilot.fixtures import PRACTITIONER_DR_SMITH
from copilot.tools import (
    make_tools,
    set_active_patient_id,
    set_active_user_id,
)


def _settings(*, admins: tuple[str, ...] = ()) -> Settings:
    return Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        USE_FIXTURE_FHIR=True,
        COPILOT_ADMIN_USER_IDS=",".join(admins),
    )


@pytest.fixture(autouse=True)
def _reset_context():
    set_active_patient_id(None)
    set_active_user_id(None)
    yield
    set_active_patient_id(None)
    set_active_user_id(None)


def _tool_by_name(name: str, *, admins: tuple[str, ...] = ()):
    for tool in make_tools(_settings(admins=admins)):
        if tool.name == name:
            return tool
    raise KeyError(name)


# ---------------------------------------------------------------------------
# Legacy SMART-pin path (no active user_id)
# ---------------------------------------------------------------------------


async def test_tool_rejects_mismatched_patient_id_when_no_user_bound() -> None:
    set_active_patient_id("fixture-1")
    tool = _tool_by_name("get_active_medications")

    result = await tool.ainvoke({"patient_id": "intruder-2"})

    assert result["ok"] is False
    assert result["error"] == "patient_context_mismatch"
    assert result["rows"] == []


async def test_tool_allows_matching_patient_id_when_no_user_bound() -> None:
    set_active_patient_id("fixture-1")
    tool = _tool_by_name("get_active_medications")

    result = await tool.ainvoke({"patient_id": "fixture-1"})

    assert result["ok"] is True
    assert any(r["fhir_ref"].startswith("MedicationRequest/") for r in result["rows"])


async def test_tool_allows_when_no_context_bound_at_all() -> None:
    """Unit tests / scripts that don't bind a context still work."""
    tool = _tool_by_name("get_patient_demographics")

    result = await tool.ainvoke({"patient_id": "fixture-1"})

    assert result["ok"] is True


# ---------------------------------------------------------------------------
# CareTeam-gate path (active user_id bound)
# ---------------------------------------------------------------------------


async def test_tool_allows_in_team_patient_for_bound_user() -> None:
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool_by_name("get_active_medications")

    result = await tool.ainvoke({"patient_id": "fixture-1"})

    assert result["ok"] is True
    assert any(r["fhir_ref"].startswith("MedicationRequest/") for r in result["rows"])


async def test_tool_denies_out_of_team_patient_for_bound_user() -> None:
    """fixture-2 is NOT on dr_smith's care team — gate returns careteam_denied."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool_by_name("get_active_medications")

    result = await tool.ainvoke({"patient_id": "fixture-2"})

    assert result["ok"] is False
    assert result["error"] == "careteam_denied"
    assert result["rows"] == []


async def test_admin_user_bypasses_care_team_gate() -> None:
    admin_id = "practitioner-admin"
    set_active_user_id(admin_id)
    tool = _tool_by_name("get_active_medications", admins=(admin_id,))

    # fixture-2 has no CareTeam row of its own; admin still reaches it.
    result = await tool.ainvoke({"patient_id": "fixture-2"})

    assert result["ok"] is True


async def test_all_patient_scoped_tools_enforce_gate_for_bound_user() -> None:
    """Every patient-scoped tool must consult the CareTeam gate. Panel-spanning
    tools like ``get_my_patient_list`` are intentionally exempt."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tools = make_tools(_settings())
    for tool in tools:
        properties = tool.args_schema.model_json_schema().get("properties", {})
        if "patient_id" not in properties:
            continue
        kwargs = {"patient_id": "fixture-2"}
        if "hours" in properties:
            kwargs["hours"] = 24
        result = await tool.ainvoke(kwargs)
        assert result["ok"] is False, f"{tool.name} did not enforce CareTeam gate"
        assert result["error"] == "careteam_denied", tool.name
