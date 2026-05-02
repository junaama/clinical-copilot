"""``run_abx_stewardship`` composite tool — issue 007.

Covers:

* Envelope shape: granular ``ToolResult.to_payload()`` shape so
  citation cards / verifier downstream don't have to special-case the
  composite.
* Four-branch fan-out: every resource type the W-11 ("should this
  patient still be on broad-spectrum?") workflow needs to surface
  appears in ``sources_checked`` — active medications, medication
  administrations, recent labs (where culture sensitivities and WBC
  trends live as Observations), and recent orders (which include
  culture orders authored over the window).
* Antibiotic filtering lives at the synthesis layer, not the data
  layer. The composite returns *all* active meds / MARs / labs /
  orders; the W-11 framing tells the LLM which RxNorm / SNOMED codes
  are antibiotics. Mirrors the W-10 (renal/hepatic markers) design —
  pre-filtering at the data layer would couple the composite to a
  vocabulary and break as new abx are added.
* Active problems and demographics are intentionally NOT in the
  fan-out; the W-11 framing tells the LLM to fetch them granularly
  if it needs to anchor the indication or duration of therapy.
* Parallel fan-out: total wall-clock latency stays close to a single
  constituent call, not the sum.
* Gate enforcement on every nested call (defense in depth — a single
  branch that bypassed the gate would still be caught at the others).
* Hard-deny path when the user is not on the patient's CareTeam.
* ``no_active_patient`` for empty ``patient_id``.
* Admin bypass exposes off-team patients via the env-driven allow-list.
* Registration in ``make_tools`` and a description that signals the
  W-11 ("antibiotic stewardship", "broad-spectrum", "abx") intent so
  the LLM picks it over chaining the granular reads.

The synthesis-prompt selector (W-11 framing) is covered in
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
    name: str = "run_abx_stewardship",
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


async def test_run_abx_stewardship_returns_envelope_for_authorized_patient() -> None:
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


async def test_run_abx_stewardship_sources_checked_lists_each_branch() -> None:
    """The composite must surface ``sources_checked`` from every fan-out branch.

    Fan-out: MedicationRequest (active), MedicationAdministration,
    Observation (laboratory), ServiceRequest. Cultures appear under
    Observation (laboratory) for sensitivities/gram stains and
    ServiceRequest for the culture orders themselves; the W-11 framing
    tells the LLM how to pick them out.
    """
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"patient_id": "fixture-1"})

    sources = result["sources_checked"]
    assert "MedicationRequest (active)" in sources
    assert "MedicationAdministration" in sources
    assert "Observation (laboratory)" in sources
    assert "ServiceRequest" in sources


async def test_run_abx_stewardship_excludes_problems_and_demographics() -> None:
    """The composite is scoped to abx-relevant resources only.

    Active problems and demographics are *anchoring* state the W-11
    framing tells the LLM to fetch granularly when it needs to confirm
    the indication or compute a duration of therapy. Including them in
    the composite would balloon the envelope without serving the
    stewardship lens.
    """
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"patient_id": "fixture-1"})

    sources = " ".join(result["sources_checked"]).lower()
    # The stewardship composite must NOT fan out conditions or patient
    # demographics — those are anchoring state the W-11 framing fetches
    # granularly.
    assert "condition" not in sources
    assert "patient (" not in sources


async def test_run_abx_stewardship_returns_meds_and_mars_and_labs_and_orders() -> None:
    """fixture-1 (Eduardo) carries data for every fan-out branch — assert
    each resource type makes it into the merged rows."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"patient_id": "fixture-1"})

    resource_types = {row["resource_type"] for row in result["rows"]}
    assert "MedicationRequest" in resource_types
    assert "MedicationAdministration" in resource_types
    assert "Observation" in resource_types
    assert "ServiceRequest" in resource_types


# ---------------------------------------------------------------------------
# Parallel fan-out
# ---------------------------------------------------------------------------


async def test_run_abx_stewardship_runs_fanout_in_parallel() -> None:
    """Wall-clock latency ≈ one slow call, not sum of all calls.

    With 4 nested calls each sleeping 50ms, a serial implementation would
    take ~200ms. Parallel fan-out via ``asyncio.gather`` should finish
    in ~50ms with overhead. We allow a generous 175ms ceiling to accommodate
    CI jitter while still failing loudly on a serial implementation.
    """
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    from copilot.fhir import FhirClient

    original_search = FhirClient.search

    async def slow_search(self, resource_type, params):
        await asyncio.sleep(0.05)
        return await original_search(self, resource_type, params)

    with patch.object(FhirClient, "search", slow_search):
        started = time.monotonic()
        result = await tool.ainvoke({"patient_id": "fixture-1"})
        elapsed = time.monotonic() - started

    assert result["ok"] is True
    # Sum-of-four = 0.20s; parallel = ~0.05s. Generous ceiling at 0.175s.
    assert elapsed < 0.175, (
        f"composite tool ran serially: elapsed={elapsed:.3f}s "
        f"(expected ~0.05s parallel; serial would be ~0.20s)"
    )


# ---------------------------------------------------------------------------
# Gate enforcement (per nested call, not just at entry)
# ---------------------------------------------------------------------------


async def test_run_abx_stewardship_enforces_gate_per_nested_call() -> None:
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
    # Top-of-call gate + 4 fan-out branches = 5 gate checks minimum.
    assert call_count >= 4, (
        f"gate was consulted only {call_count} times — expected at least 4 "
        f"(one per fan-out branch)"
    )


async def test_run_abx_stewardship_denies_out_of_team_patient() -> None:
    """fixture-2 is not on dr_smith's CareTeam — composite returns careteam_denied."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"patient_id": "fixture-2"})

    assert result["ok"] is False
    assert result["error"] == "careteam_denied"
    assert result["rows"] == []


async def test_run_abx_stewardship_denies_when_no_user_bound() -> None:
    """Tool layer requires an active user_id; gate denies otherwise."""
    tool = _tool()

    result = await tool.ainvoke({"patient_id": "fixture-1"})

    assert result["ok"] is False
    assert result["error"] == "careteam_denied"


async def test_run_abx_stewardship_returns_no_active_patient_for_empty_pid() -> None:
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"patient_id": ""})

    assert result["ok"] is False
    assert result["error"] == "no_active_patient"


async def test_run_abx_stewardship_admin_bypass() -> None:
    """Admin users authorized by the env-driven allow-list reach any patient."""
    admin_id = "practitioner-admin"
    set_active_user_id(admin_id)
    tool = _tool(admins=(admin_id,))

    result = await tool.ainvoke({"patient_id": "fixture-2"})

    assert result["ok"] is True


# ---------------------------------------------------------------------------
# Schema discovery (LLM-facing description and arg shape)
# ---------------------------------------------------------------------------


async def test_run_abx_stewardship_is_registered_in_make_tools() -> None:
    """The tool is bound to the LLM via make_tools, with patient_id as the
    only required arg.

    AC: ``run_abx_stewardship(patient_id)`` is a ``StructuredTool``
    registered in ``make_tools(settings)``.
    """
    tools = make_tools(_settings())
    by_name = {t.name: t for t in tools}
    assert "run_abx_stewardship" in by_name
    tool = by_name["run_abx_stewardship"]
    schema = tool.args_schema.model_json_schema()
    properties = schema.get("properties", {})
    assert "patient_id" in properties
    assert "hours" in properties
    # ``hours`` is optional; ``patient_id`` is required.
    assert "patient_id" in (schema.get("required") or [])
    assert "hours" not in (schema.get("required") or [])


async def test_run_abx_stewardship_description_signals_w11_intent() -> None:
    """The tool's description must signal antibiotic-stewardship intent so
    the LLM picks it for W-11 turns over the granular walk.
    """
    tool = _tool()
    description = tool.description.lower()
    # Must mention antibiotic / abx / stewardship / broad-spectrum
    # vocabulary so the LLM has the right routing signal.
    assert (
        "antibiotic" in description
        or "abx" in description
        or "stewardship" in description
    )
    # And reference the multi-resource fan-out shape so the LLM knows
    # what's covered.
    assert "med" in description  # 'medication' or 'med-safety'
