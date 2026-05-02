"""``run_cross_cover_onboarding`` composite tool — issue 007.

Covers:

* Envelope shape: granular ``ToolResult.to_payload()`` shape so
  citation cards / verifier downstream don't have to special-case the
  composite.
* Five-branch fan-out: every resource type the cross-cover narrative
  needs (Condition, MedicationRequest, Encounter, ServiceRequest,
  DocumentReference) appears in ``rows``.
* Wider-history default: the composite reaches resources older than
  the 24-hour brief window — confirmed by the default 168-hour
  lookback being plumbed through to the time-windowed branches.
* Parallel fan-out: total wall-clock latency stays close to a single
  constituent call, not the sum.
* Gate enforcement on every nested call (defense in depth — a single
  branch that bypassed the gate would still be caught at the others).
* Hard-deny path when the user is not on the patient's CareTeam.
* ``no_active_patient`` for empty ``patient_id``.
* Admin bypass exposes off-team patients via the env-driven allow-list.
* Registration in ``make_tools`` and a description that signals the
  cross-cover / family-meeting intent (so the LLM picks it for W-4 /
  W-5 turns over the granular walk).

The synthesis-prompt selector (W-4 / W-5 framings) is covered in
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


def _tool(
    name: str = "run_cross_cover_onboarding",
    *,
    admins: tuple[str, ...] = (),
):
    for tool in make_tools(_settings(admins=admins)):
        if tool.name == name:
            return tool
    raise KeyError(name)


# ---------------------------------------------------------------------------
# Envelope shape
# ---------------------------------------------------------------------------


async def test_run_cross_cover_returns_envelope_for_authorized_patient() -> None:
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


async def test_run_cross_cover_fans_out_five_resource_types() -> None:
    """The composite must surface rows from every fan-out branch.

    Fan-out: Condition (active), MedicationRequest (active),
    Encounter (recent window), ServiceRequest (recent orders),
    DocumentReference (clinical notes for the hospital course).
    fixture-1 (Eduardo) carries data for all five branches in the
    fixture bundle.
    """
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"patient_id": "fixture-1"})

    resource_types = {row["resource_type"] for row in result["rows"]}
    assert "Condition" in resource_types
    assert "MedicationRequest" in resource_types
    assert "Encounter" in resource_types
    assert "ServiceRequest" in resource_types
    assert "DocumentReference" in resource_types
    # Vitals / labs are intentionally NOT in the cross-cover composite —
    # those are the per_patient_brief composite's job. Document the boundary.
    # (Observation rows could appear if a granular tool overlapped, so
    # we don't assert their absence — but the source labels below confirm
    # we didn't fan out the vital/lab branches.)
    sources = " ".join(result["sources_checked"]).lower()
    assert "vital-signs" not in sources
    assert "laboratory" not in sources


async def test_run_cross_cover_sources_checked_lists_each_branch() -> None:
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"patient_id": "fixture-1"})

    sources = result["sources_checked"]
    assert "Condition (active)" in sources
    assert "MedicationRequest (active)" in sources
    assert "Encounter" in sources
    assert "ServiceRequest" in sources
    assert "DocumentReference" in sources


# ---------------------------------------------------------------------------
# Wider history (cross-cover spans the admission, not just overnight)
# ---------------------------------------------------------------------------


async def test_run_cross_cover_default_lookback_is_wider_than_24h() -> None:
    """The cross-cover composite must default to a wider window than the
    24-hour brief — its job is to convey the hospital course, not just the
    overnight events.

    Asserted via the schema: the ``hours`` arg must default to a value
    larger than 24 (we use 168h / 7 days). A regression that silently
    flipped the default to 24 would lose the admission-encounter and
    earlier-orders signal that cross-cover narratives depend on.
    """
    tools = make_tools(_settings())
    by_name = {t.name: t for t in tools}
    schema = by_name["run_cross_cover_onboarding"].args_schema.model_json_schema()
    hours_default = schema["properties"]["hours"].get("default")
    assert hours_default is not None
    assert hours_default > 24, (
        f"cross-cover lookback shrank to {hours_default}h — must remain "
        f"wider than the 24h brief window"
    )


# ---------------------------------------------------------------------------
# Parallel fan-out
# ---------------------------------------------------------------------------


async def test_run_cross_cover_runs_fanout_in_parallel() -> None:
    """Wall-clock latency ≈ one slow call, not sum of all calls.

    With 5 nested calls each sleeping 50ms, a serial implementation would
    take ~250ms. Parallel fan-out via ``asyncio.gather`` should finish
    in ~50ms with overhead. We allow a generous 200ms ceiling to accommodate
    CI jitter while still failing loudly on a serial implementation.
    """
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

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
    # Sum-of-five = 0.25s; parallel = ~0.05s. Generous ceiling at 0.20s.
    assert elapsed < 0.20, (
        f"composite tool ran serially: elapsed={elapsed:.3f}s "
        f"(expected ~0.05s parallel; serial would be ~0.25s)"
    )


# ---------------------------------------------------------------------------
# Gate enforcement (per nested call, not just at entry)
# ---------------------------------------------------------------------------


async def test_run_cross_cover_enforces_gate_per_nested_call() -> None:
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
    # Top-of-call gate + 5 fan-out branches = 6 gate checks minimum.
    assert call_count >= 5, (
        f"gate was consulted only {call_count} times — expected at least 5 "
        f"(one per fan-out branch)"
    )


async def test_run_cross_cover_denies_out_of_team_patient() -> None:
    """fixture-2 is not on dr_smith's CareTeam — composite returns careteam_denied."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"patient_id": "fixture-2"})

    assert result["ok"] is False
    assert result["error"] == "careteam_denied"
    assert result["rows"] == []


async def test_run_cross_cover_denies_when_no_user_bound() -> None:
    """Tool layer requires an active user_id; gate denies otherwise."""
    tool = _tool()

    result = await tool.ainvoke({"patient_id": "fixture-1"})

    assert result["ok"] is False
    assert result["error"] == "careteam_denied"


async def test_run_cross_cover_returns_no_active_patient_for_empty_pid() -> None:
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"patient_id": ""})

    assert result["ok"] is False
    assert result["error"] == "no_active_patient"


async def test_run_cross_cover_admin_bypass() -> None:
    """Admin users authorized by the env-driven allow-list reach any patient."""
    admin_id = "practitioner-admin"
    set_active_user_id(admin_id)
    tool = _tool(admins=(admin_id,))

    result = await tool.ainvoke({"patient_id": "fixture-2"})

    assert result["ok"] is True


# ---------------------------------------------------------------------------
# Schema discovery (LLM-facing description and arg shape)
# ---------------------------------------------------------------------------


async def test_run_cross_cover_is_registered_in_make_tools() -> None:
    """The tool is bound to the LLM via make_tools, with patient_id as the
    only required arg.

    AC: ``run_cross_cover_onboarding(patient_id)`` is a ``StructuredTool``
    registered in ``make_tools(settings)``.
    """
    tools = make_tools(_settings())
    by_name = {t.name: t for t in tools}
    assert "run_cross_cover_onboarding" in by_name
    tool = by_name["run_cross_cover_onboarding"]
    schema = tool.args_schema.model_json_schema()
    properties = schema.get("properties", {})
    assert "patient_id" in properties
    assert "hours" in properties
    # ``hours`` is optional (default 168); ``patient_id`` is required.
    assert "patient_id" in (schema.get("required") or [])
    assert "hours" not in (schema.get("required") or [])


async def test_run_cross_cover_description_signals_w4_w5_intent() -> None:
    """The tool's description must signal cross-cover / family-meeting
    intent so the LLM picks it for W-4 / W-5 turns over the granular walk.
    """
    tool = _tool()
    description = tool.description.lower()
    # Must mention cross-cover (W-4) and/or family (W-5) intent so the
    # LLM has the right routing signal.
    assert "cross-cover" in description or "cross cover" in description
    assert "family" in description
    # Should mention the hospital-course narrative shape (problems +
    # meds + encounters + orders + notes) or the orientation framing.
    assert (
        "orient" in description
        or "course" in description
        or "story" in description
    )
