"""``run_panel_med_safety`` composite tool — issue 007.

Mirrors ``test_panel_triage.py`` but with the W-10 fan-out shape:
``get_active_medications`` + ``get_recent_labs`` per pid, two branches
instead of three. Covers:

* Envelope shape (granular ``ToolResult.to_payload()`` shape, panel
  source label included).
* Per-pid fan-out across the user's CareTeam roster (Medication +
  Observation rows present).
* Parallel fan-out: the per-pid sub-fan-out runs concurrently across
  pids. Asserted via a max-concurrent counter wrapping
  ``CareTeamGate.assert_authorized``, which is more reliable than a
  wall-clock ceiling for a 2-branch composite where each branch is one
  fast FHIR search.
* Gate enforcement on every nested per-pid call (defense in depth — the
  panel itself is intrinsically CareTeam-bounded by ``list_panel``, but
  per-call gating catches a buggy widening).
* Panel-bounded scoping: a non-admin user only sees their own roster's
  patients; out-of-team pids never appear in any row.
* Empty-panel returns an ok empty envelope, not an error.
* Admin bypass exposes the full panel.
* Registration in ``make_tools`` and a description that signals the
  pharmacist / med-safety intent (so the LLM picks it for W-10 turns).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from copilot.config import Settings
from copilot.fixtures import DR_SMITH_PANEL, PRACTITIONER_DR_SMITH
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


def _tool(name: str = "run_panel_med_safety", *, admins: tuple[str, ...] = ()):
    for tool in make_tools(_settings(admins=admins)):
        if tool.name == name:
            return tool
    raise KeyError(name)


# ---------------------------------------------------------------------------
# Envelope shape
# ---------------------------------------------------------------------------


async def test_run_panel_med_safety_returns_granular_envelope_for_authorized_user() -> None:
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({})

    assert result["ok"] is True
    assert result["error"] is None
    # Same envelope shape as a granular tool (rows, sources_checked,
    # latency_ms, error, ok).
    assert isinstance(result["rows"], list)
    assert isinstance(result["sources_checked"], list)
    assert isinstance(result["latency_ms"], int)
    # The top-level "panel" sentinel is included so the LLM can name the
    # source in its synthesis.
    assert "CareTeam (panel)" in result["sources_checked"]


async def test_run_panel_med_safety_fans_out_meds_and_labs_per_pid() -> None:
    """Each pid in the panel gets active meds + recent labs.

    fixture-1 (Eduardo) carries 4 active meds and 2 recent labs (Cr,
    K+) in the bundle, so MedicationRequest and Observation rows must
    both appear in the merged envelope. The other panel pids may have
    no meds and no labs, but the source labels still must include both
    resource types' source sentinels.
    """
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({})

    resource_types = {row["resource_type"] for row in result["rows"]}
    assert "MedicationRequest" in resource_types
    assert "Observation" in resource_types
    # Source labels surface both fan-out branches so the LLM can name them.
    sources_lower = " ".join(result["sources_checked"]).lower()
    assert "medicationrequest" in sources_lower
    assert "observation" in sources_lower
    assert "laboratory" in sources_lower


# ---------------------------------------------------------------------------
# Panel-bounded scoping
# ---------------------------------------------------------------------------


async def test_run_panel_med_safety_only_returns_panel_patients_for_non_admin() -> None:
    """dr_smith's panel is fixture-1, fixture-3, fixture-5. Out-of-team pids
    (fixture-2, fixture-4) must not appear in any row's fhir_ref."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({})

    pids_seen: set[str] = set()
    for row in result["rows"]:
        ref = row.get("fhir_ref") or ""
        # MedicationRequest / Observation rows reference the patient
        # only through the subject field (not the row's fhir_ref). The
        # rows themselves don't carry a patient_id field. Cross-check
        # via the bundle: assert no row's fhir_ref id matches a known
        # out-of-team resource id.
        forbidden_resource_ids = {
            "obs-bp-maya-1",     # Maya's vital — would be excluded by lab category anyway
            "obs-temp-linda-1",  # Linda's vital — same
            "obs-wbc-linda-1",   # Linda's lab — out-of-team
            "cond-postop-maya",  # Maya
            "cond-pna-linda",    # Linda
        }
        for fid in forbidden_resource_ids:
            assert fid not in ref, (
                f"out-of-team resource leaked into med-safety rows: {ref}"
            )
        # Track resolved patient ids by examining MedicationRequest patient ids.
        # For meds/labs the pid isn't in the row directly, so this set
        # stays empty for this composite — the forbidden-id check is the
        # primary scoping assertion.
        if ref.startswith("Patient/"):
            pids_seen.add(ref.removeprefix("Patient/"))

    if pids_seen:
        assert pids_seen.issubset(set(DR_SMITH_PANEL))


async def test_run_panel_med_safety_admin_bypass_returns_full_panel() -> None:
    """Admin allow-list users see every patient in fixtures.CARE_TEAM_PANEL.

    Asserted via the lab row from Linda (fixture-4, NOT on dr_smith's
    panel) — Linda's WBC observation must appear when admin runs the
    composite, and must be absent when dr_smith does.
    """
    admin_id = "practitioner-admin"
    set_active_user_id(admin_id)
    tool = _tool(admins=(admin_id,))

    result = await tool.ainvoke({})

    assert result["ok"] is True
    fhir_refs = " ".join(row.get("fhir_ref") or "" for row in result["rows"])
    # Linda's WBC is the only fixture lab tied to fixture-4.
    assert "obs-wbc-linda-1" in fhir_refs, (
        "admin run did not surface Linda's lab — admin bypass not exposing "
        "the full panel"
    )
    # And dr_smith doesn't see Linda's lab.
    set_active_user_id(PRACTITIONER_DR_SMITH)
    dr_smith_tool = _tool()
    dr_result = await dr_smith_tool.ainvoke({})
    dr_refs = " ".join(row.get("fhir_ref") or "" for row in dr_result["rows"])
    assert "obs-wbc-linda-1" not in dr_refs


async def test_run_panel_med_safety_returns_empty_envelope_for_empty_panel() -> None:
    """Unbound user → list_panel returns []. Composite returns ok: True
    with zero rows so the LLM can say 'no patients' rather than refuse."""
    tool = _tool()  # no user bound

    result = await tool.ainvoke({})

    assert result["ok"] is True
    assert result["rows"] == []
    assert result["error"] is None
    assert "CareTeam (panel)" in result["sources_checked"]


# ---------------------------------------------------------------------------
# Parallel fan-out (concurrency-counter approach)
# ---------------------------------------------------------------------------


async def test_run_panel_med_safety_runs_outer_fanout_in_parallel() -> None:
    """The outer per-pid fan-out must run concurrently, not serially.

    What this test catches: a regression that swapped the outer
    ``asyncio.gather(*[_per_pid(...)])`` for a serial loop. Wall-clock
    is unreliable for this composite because each per-pid branch is
    one fast FHIR search (~ms), so the parallel-vs-serial gap collapses
    into CI jitter. Instead, instrument
    ``CareTeamGate.assert_authorized`` to track max concurrent
    invocations: if the outer gather is parallel, multiple per-pid
    sub-fan-outs run concurrently and we observe several gate calls
    in flight at once. A serial regression caps max-concurrent at 1
    (or 2 with the inner gather).
    """
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    from copilot.care_team import CareTeamGate

    original = CareTeamGate.assert_authorized
    in_flight = 0
    max_in_flight = 0

    async def tracking_assert(self, user_id, patient_id):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            # Yield to the loop so siblings have a chance to enter
            # before this one returns. Otherwise the gate's await on
            # the FHIR call (which uses a fixture short-circuit) would
            # complete before any sibling task is scheduled.
            import asyncio

            await asyncio.sleep(0)
            return await original(self, user_id, patient_id)
        finally:
            in_flight -= 1

    with patch.object(CareTeamGate, "assert_authorized", tracking_assert):
        result = await tool.ainvoke({})

    assert result["ok"] is True
    # 3 patients x 2 branches each = 6 gate calls. With outer-parallel
    # fan-out we expect at least the panel size (3) of them to be in
    # flight simultaneously. A serial outer would cap at 2 (the inner
    # gather over 2 branches).
    assert max_in_flight >= len(DR_SMITH_PANEL), (
        f"outer fan-out ran serially: max concurrent gate calls "
        f"observed = {max_in_flight}, expected >= {len(DR_SMITH_PANEL)} "
        f"(panel size)"
    )


# ---------------------------------------------------------------------------
# Gate enforcement (per nested call, not just at entry)
# ---------------------------------------------------------------------------


async def test_run_panel_med_safety_enforces_gate_per_nested_call() -> None:
    """Every per-pid nested call goes through ``CareTeamGate.assert_authorized``.

    Defense in depth: ``list_panel`` is intrinsically CareTeam-bounded
    so the gate would never deny one of its outputs, but the per-call
    gating catches a buggy refactor that widened ``list_panel`` to
    return out-of-team rows. Counting spy.
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
        result = await tool.ainvoke({})

    assert result["ok"] is True
    # 3 patients x 2 per-pid branches = 6 gate consultations minimum.
    assert call_count >= len(DR_SMITH_PANEL) * 2, (
        f"gate was consulted only {call_count} times — expected at least "
        f"{len(DR_SMITH_PANEL) * 2} (2 branches x 3 pids on dr_smith's panel)"
    )


# ---------------------------------------------------------------------------
# Schema discovery (LLM-facing description and arg shape)
# ---------------------------------------------------------------------------


async def test_run_panel_med_safety_is_registered_in_make_tools() -> None:
    """The tool is bound to the LLM via make_tools.

    AC: ``run_panel_med_safety()`` is a ``StructuredTool`` registered in
    ``make_tools(settings)``.
    """
    tools = make_tools(_settings())
    by_name = {t.name: t for t in tools}
    assert "run_panel_med_safety" in by_name
    tool = by_name["run_panel_med_safety"]
    schema = tool.args_schema.model_json_schema()
    # ``hours`` is the only argument and it's optional.
    properties = schema.get("properties", {})
    assert "patient_id" not in properties
    assert "hours" in properties
    # ``hours`` should not appear in required so the LLM can call with
    # zero arguments.
    assert "hours" not in (schema.get("required") or [])


async def test_run_panel_med_safety_description_signals_pharmacist_intent() -> None:
    """The tool's description must signal med-safety / pharmacist intent
    so the LLM picks it for W-10 turns over the granular walk."""
    tool = _tool()
    description = tool.description.lower()
    assert "panel" in description
    assert "med" in description  # 'medication' or 'med-safety'
    assert (
        "safety" in description
        or "pharmacist" in description
        or "review" in description
    )
