"""Tool-layer authorization gate (issue 003 final shape).

The legacy SMART-pin shim is gone — every patient-data tool consults the
CareTeam gate, with the active practitioner bound on the ``user_id``
contextvar by the graph (or by tests).

* In-team patient → ``ok=True``, real rows.
* Out-of-team patient → ``careteam_denied`` refusal payload.
* No ``user_id`` bound → gate denies (callers must bind a practitioner
  even in tests).
* ``COPILOT_ADMIN_USER_IDS`` allow-list bypasses the gate so admin actions
  are still authorized while remaining auditable.

The contract test at the bottom walks every patient-scoped tool to ensure
the gate is consulted across the surface.
"""

from __future__ import annotations

import pytest

from copilot.config import Settings
from copilot.fixtures import PRACTITIONER_DR_SMITH
from copilot.tools import (
    make_tools,
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
    set_active_user_id(None)
    yield
    set_active_user_id(None)


def _tool_by_name(name: str, *, admins: tuple[str, ...] = ()):
    for tool in make_tools(_settings(admins=admins)):
        if tool.name == name:
            return tool
    raise KeyError(name)


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


async def test_tool_denies_when_no_user_bound() -> None:
    """The legacy SMART-pin fallback was removed in issue 003; tools now
    require an active user_id to authorize. An empty user_id collapses to
    careteam_denied."""
    tool = _tool_by_name("get_active_medications")

    result = await tool.ainvoke({"patient_id": "fixture-1"})

    assert result["ok"] is False
    assert result["error"] == "careteam_denied"


async def test_tool_returns_no_active_patient_for_empty_pid() -> None:
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool_by_name("get_active_medications")

    result = await tool.ainvoke({"patient_id": ""})

    assert result["ok"] is False
    assert result["error"] == "no_active_patient"


async def test_all_patient_scoped_tools_enforce_gate_for_bound_user() -> None:
    """Every patient-scoped tool must consult the CareTeam gate. Panel-spanning
    tools like ``get_my_patient_list`` and ``resolve_patient`` are
    intentionally exempt."""
    from datetime import UTC, datetime, timedelta

    set_active_user_id(PRACTITIONER_DR_SMITH)
    tools = make_tools(_settings())
    panel_tools = {"get_my_patient_list", "resolve_patient"}
    since_iso = (datetime.now(UTC) - timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    for tool in tools:
        if tool.name in panel_tools:
            continue
        properties = tool.args_schema.model_json_schema().get("properties", {})
        if "patient_id" not in properties:
            continue
        kwargs: dict[str, object] = {"patient_id": "fixture-2"}
        if "hours" in properties:
            kwargs["hours"] = 24
        if "since" in properties:
            kwargs["since"] = since_iso
        if "domain" in properties:
            kwargs["domain"] = "cardiology"
        result = await tool.ainvoke(kwargs)
        assert result["ok"] is False, f"{tool.name} did not enforce CareTeam gate"
        assert result["error"] == "careteam_denied", tool.name
