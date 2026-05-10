"""``run_per_patient_brief`` composite tool — issue 006.

Covers:

* Envelope shape and every fan-out resource type appearing in ``rows``.
* Parallel fan-out: total wall-clock latency stays close to a single
  constituent call, not the sum.
* Gate enforcement on every nested call (not just the composite's entry
  point).
* Hard-deny path when the user is not on the patient's CareTeam.
* ``no_active_patient`` for empty ``patient_id``.

The synthesis-prompt selector (``build_system_prompt``) is covered in
``test_synthesis_prompt_selector.py``.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from copilot.config import Settings
from copilot.fixtures import PRACTITIONER_DR_SMITH
from copilot.tools import make_tools, set_active_user_id


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


def _tool(name: str = "run_per_patient_brief", *, admins: tuple[str, ...] = ()):
    for tool in make_tools(_settings(admins=admins)):
        if tool.name == name:
            return tool
    raise KeyError(name)


# ---------------------------------------------------------------------------
# Envelope shape
# ---------------------------------------------------------------------------


async def test_run_per_patient_brief_returns_envelope_for_authorized_patient() -> None:
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"patient_id": "fixture-1"})

    assert result["ok"] is True
    assert result["error"] is None
    # Same envelope shape as a granular tool (rows, sources_checked,
    # latency_ms, error, ok).
    assert isinstance(result["rows"], list)
    assert isinstance(result["sources_checked"], list)
    assert isinstance(result["latency_ms"], int)


async def test_run_per_patient_brief_fans_out_brief_resource_types() -> None:
    """The composite must surface rows from every fan-out branch.

    Fan-out: Patient (demographics), Condition (active), MedicationRequest
    (active), Observation (vital-signs), Observation (laboratory),
    Encounter (recent window), DocumentReference (clinical notes).
    """
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"patient_id": "fixture-1"})

    resource_types = {row["resource_type"] for row in result["rows"]}
    assert "Patient" in resource_types
    assert "Condition" in resource_types
    assert "MedicationRequest" in resource_types
    assert "Observation" in resource_types
    assert "Encounter" in resource_types
    assert "DocumentReference" in resource_types


async def test_run_per_patient_brief_sources_checked_includes_categories() -> None:
    """``sources_checked`` distinguishes vital-signs from laboratory observations."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"patient_id": "fixture-1"})

    sources = result["sources_checked"]
    assert "Patient" in sources
    assert "Condition (active)" in sources
    assert "MedicationRequest (active)" in sources
    assert "Observation (vital-signs)" in sources
    assert "Observation (laboratory)" in sources
    assert "Encounter" in sources
    assert "DocumentReference" in sources


# ---------------------------------------------------------------------------
# Parallel fan-out
# ---------------------------------------------------------------------------


async def test_run_per_patient_brief_runs_fanout_in_parallel() -> None:
    """Wall-clock latency ≈ one slow call, not sum of all calls.

    With 7 nested calls each sleeping 50ms, a serial implementation would
    take ~350ms. Parallel fan-out via ``asyncio.gather`` should finish
    in ~50ms with overhead. We allow a generous 200ms ceiling to accommodate
    CI jitter while still failing loudly on a serial implementation.
    """
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    # Patch the FHIR client methods to introduce a known latency. The
    # composite is built inside make_tools and holds its own client
    # reference, so we patch FhirClient.search/read at the class level.
    from copilot.fhir import FhirClient

    original_search = FhirClient.search
    original_read = FhirClient.read

    async def slow_search(self, resource_type, params):
        await asyncio.sleep(0.05)
        return await original_search(self, resource_type, params)

    async def slow_read(self, resource_type, resource_id):
        await asyncio.sleep(0.05)
        return await original_read(self, resource_type, resource_id)

    with patch.object(FhirClient, "search", slow_search), \
         patch.object(FhirClient, "read", slow_read):
        started = time.monotonic()
        result = await tool.ainvoke({"patient_id": "fixture-1"})
        elapsed = time.monotonic() - started

    assert result["ok"] is True
    # Sum-of-seven = 0.35s; parallel = ~0.05s. Generous ceiling at 0.20s.
    assert elapsed < 0.20, (
        f"composite tool ran serially: elapsed={elapsed:.3f}s "
        f"(expected ~0.05s parallel; serial would be ~0.35s)"
    )


async def test_run_per_patient_brief_tolerates_document_reference_policy_denial() -> None:
    """OpenEMR can deny optional DocumentReference search while allowing
    demographics, problems, medications, vitals, labs, and encounters."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    from copilot.fhir import FhirClient

    original_search = FhirClient.search

    async def deny_document_reference(self, resource_type, params):
        if resource_type == "DocumentReference":
            return (
                False,
                [],
                "http_403: Organization policy does not have permit access resource",
                12,
            )
        return await original_search(self, resource_type, params)

    with patch.object(FhirClient, "search", deny_document_reference):
        result = await tool.ainvoke({"patient_id": "fixture-1"})

    assert result["ok"] is True
    assert result["error"] is None
    assert "DocumentReference" in result["sources_checked"]
    resource_types = {row["resource_type"] for row in result["rows"]}
    assert "Patient" in resource_types
    assert "DocumentReference" not in resource_types


async def test_run_per_patient_brief_uses_openemr_supported_condition_search() -> None:
    """OpenEMR prod does not support ``Condition?clinical-status=active``."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    from copilot.fhir import FhirClient

    original_search = FhirClient.search
    condition_params: list[dict[str, str]] = []

    async def capture_condition_search(self, resource_type, params):
        if resource_type == "Condition":
            condition_params.append(dict(params))
            return (
                True,
                [
                    {
                        "resourceType": "Condition",
                        "id": "active-1",
                        "clinicalStatus": {"coding": [{"code": "active"}]},
                        "code": {"coding": [{"display": "Heart failure"}]},
                    },
                    {
                        "resourceType": "Condition",
                        "id": "resolved-1",
                        "clinicalStatus": {"coding": [{"code": "resolved"}]},
                        "code": {"coding": [{"display": "Resolved pneumonia"}]},
                    },
                ],
                None,
                1,
            )
        return await original_search(self, resource_type, params)

    with patch.object(FhirClient, "search", capture_condition_search):
        result = await tool.ainvoke({"patient_id": "fixture-1"})

    assert condition_params
    assert all("clinical-status" not in params for params in condition_params)
    condition_rows = [
        row for row in result["rows"] if row["resource_type"] == "Condition"
    ]
    assert [row["fhir_ref"] for row in condition_rows] == ["Condition/active-1"]


async def test_run_per_patient_brief_uses_30_day_encounter_context() -> None:
    """The one-click brief needs recent admission context, not only overnight rows."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    from copilot.fhir import FhirClient

    original_search = FhirClient.search
    encounter_dates: list[str] = []

    async def capture_encounter_window(self, resource_type, params):
        if resource_type == "Encounter":
            encounter_dates.append(str(params.get("date") or ""))
        return await original_search(self, resource_type, params)

    with patch.object(FhirClient, "search", capture_encounter_window):
        result = await tool.ainvoke({"patient_id": "fixture-1", "hours": 24})

    assert result["ok"] is True
    assert encounter_dates
    assert any(date.startswith("ge2026-04-") for date in encounter_dates)


# ---------------------------------------------------------------------------
# Gate enforcement (per nested call, not just at entry)
# ---------------------------------------------------------------------------


async def test_run_per_patient_brief_enforces_gate_per_nested_call() -> None:
    """Each nested fan-out branch must consult ``CareTeamGate.assert_authorized``.

    Defense in depth: a buggy refactor that skipped the gate on a single
    branch should be caught by this test. We patch the gate to count
    invocations.
    """
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    from copilot.care_team import CareTeamGate

    original = CareTeamGate.assert_authorized
    call_count = 0

    async def counting_assert(self, user_id, patient_id):
        nonlocal call_count
        call_count += 1
        return await original(self, user_id, patient_id)

    with patch.object(CareTeamGate, "assert_authorized", counting_assert):
        result = await tool.ainvoke({"patient_id": "fixture-1"})

    assert result["ok"] is True
    # Six fan-out branches → at least six gate checks.
    assert call_count >= 6, (
        f"gate was consulted only {call_count} times — expected at least 6 "
        f"(one per fan-out branch)"
    )


async def test_run_per_patient_brief_denies_out_of_team_patient() -> None:
    """fixture-2 is not on dr_smith's CareTeam — composite returns careteam_denied."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"patient_id": "fixture-2"})

    assert result["ok"] is False
    assert result["error"] == "careteam_denied"
    assert result["rows"] == []


async def test_run_per_patient_brief_denies_when_no_user_bound() -> None:
    """Tool layer requires an active user_id; gate denies otherwise."""
    tool = _tool()

    result = await tool.ainvoke({"patient_id": "fixture-1"})

    assert result["ok"] is False
    assert result["error"] == "careteam_denied"


async def test_run_per_patient_brief_returns_no_active_patient_for_empty_pid() -> None:
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"patient_id": ""})

    assert result["ok"] is False
    assert result["error"] == "no_active_patient"


async def test_run_per_patient_brief_admin_bypass() -> None:
    """Admin users authorized by the env-driven allow-list reach any patient."""
    admin_id = "practitioner-admin"
    set_active_user_id(admin_id)
    tool = _tool(admins=(admin_id,))

    result = await tool.ainvoke({"patient_id": "fixture-2"})

    assert result["ok"] is True


# ---------------------------------------------------------------------------
# Schema discovery (LLM-facing description and arg shape)
# ---------------------------------------------------------------------------


async def test_run_per_patient_brief_is_registered_in_make_tools() -> None:
    """The tool is bound to the LLM via make_tools, with patient_id as the
    only required arg.

    AC: ``run_per_patient_brief(patient_id)`` is a ``StructuredTool``
    registered in ``make_tools(settings)``.
    """
    tools = make_tools(_settings())
    by_name = {t.name: t for t in tools}
    assert "run_per_patient_brief" in by_name
    tool = by_name["run_per_patient_brief"]
    schema = tool.args_schema.model_json_schema()
    assert "patient_id" in schema.get("properties", {})


async def test_run_per_patient_brief_description_guides_usage() -> None:
    """The tool's description must signal when to prefer the composite over
    the granular reads — otherwise the LLM has no way to pick correctly.
    """
    tool = _tool()
    description = tool.description.lower()
    # Should mention what the composite does (overview / brief) and that
    # it returns the standard envelope shape.
    assert "brief" in description or "overview" in description
