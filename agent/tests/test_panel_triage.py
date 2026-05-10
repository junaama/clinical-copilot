"""``run_panel_triage`` composite tool — issue 007.

Covers:

* Envelope shape and per-pid fan-out across the user's CareTeam roster.
* Parallel fan-out: total wall-clock latency stays close to a single
  constituent call, not the sum across pids.
* Gate enforcement on every nested per-pid call (defense in depth — the
  panel itself is intrinsically CareTeam-bounded by ``list_panel``, but
  per-call gating catches a buggy widening).
* Panel-bounded scoping: a non-admin user only sees their own roster's
  patients in ``rows``; out-of-team pids never appear.
* Empty-panel returns an ok empty envelope, not an error.
* Admin bypass exposes the full panel.
* Registration in ``make_tools`` and a description that signals the
  triage / prioritization intent (so the LLM picks it for W-1 turns).
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from copilot.config import Settings
from copilot.fixtures import CARE_TEAM_PANEL, DR_SMITH_PANEL, PRACTITIONER_DR_SMITH
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


def _tool(name: str = "run_panel_triage", *, admins: tuple[str, ...] = ()):
    for tool in make_tools(_settings(admins=admins)):
        if tool.name == name:
            return tool
    raise KeyError(name)


# ---------------------------------------------------------------------------
# Envelope shape
# ---------------------------------------------------------------------------


async def test_run_panel_triage_returns_granular_envelope_for_authorized_user() -> None:
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


async def test_run_panel_triage_fans_out_three_resource_types_per_pid() -> None:
    """Each pid in the panel gets demographics + active problems + change-signal.

    The change-signal probe itself emits one row per channel
    (Observation x 2, Encounter, DocumentReference) so the merged rows
    cover Patient, Condition, Observation, Encounter, and DocumentReference
    across the panel.
    """
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({})

    resource_types = {row["resource_type"] for row in result["rows"]}
    assert "Patient" in resource_types
    assert "Condition" in resource_types
    # change-signal counts surface as Observation/Encounter/DocumentReference rows
    assert "Observation" in resource_types
    assert "Encounter" in resource_types
    assert "DocumentReference" in resource_types


async def test_run_panel_triage_change_counts_are_marked_non_citeable() -> None:
    """Change-signal count rows guide ranking but are not fetched FHIR
    resources, so they must not look like citeable ResourceType/id refs."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({})

    refs = [row["fhir_ref"] for row in result["rows"]]
    assert not any("/_summary=" in ref or "?" in ref for ref in refs)
    assert any(ref.startswith("count-summary:") for ref in refs)


async def test_run_panel_triage_tolerates_document_reference_policy_denial() -> None:
    """DocumentReference is an optional change-signal channel; a policy 403
    should zero its count, not make the whole panel unavailable."""
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
        result = await tool.ainvoke({})

    assert result["ok"] is True
    assert result["error"] is None
    doc_rows = [
        row for row in result["rows"]
        if row["resource_type"] == "DocumentReference"
    ]
    assert doc_rows
    assert {row["fields"]["count"] for row in doc_rows} == {0}


# ---------------------------------------------------------------------------
# Panel-bounded scoping
# ---------------------------------------------------------------------------


async def test_run_panel_triage_only_returns_panel_patients_for_non_admin() -> None:
    """dr_smith's panel is fixture-1, fixture-3, fixture-5. Out-of-team pids
    (fixture-2, fixture-4) must not appear in any row."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({})

    pids_in_rows = {
        row["fields"].get("patient_id")
        for row in result["rows"]
        if "patient_id" in row["fields"]
    }
    pids_in_rows.discard(None)
    # Demographics rows carry the pid in the fhir_ref (Patient/<pid>) rather
    # than in the fields dict, so collect those too.
    for row in result["rows"]:
        ref = row.get("fhir_ref") or ""
        if ref.startswith("Patient/"):
            pids_in_rows.add(ref.removeprefix("Patient/"))

    assert pids_in_rows.issubset(set(DR_SMITH_PANEL)), (
        f"out-of-team pids leaked into panel triage rows: "
        f"{pids_in_rows - set(DR_SMITH_PANEL)}"
    )
    assert "fixture-2" not in pids_in_rows
    assert "fixture-4" not in pids_in_rows


async def test_run_panel_triage_admin_bypass_returns_full_panel() -> None:
    """Admin allow-list users see every patient in fixtures.CARE_TEAM_PANEL."""
    admin_id = "practitioner-admin"
    set_active_user_id(admin_id)
    tool = _tool(admins=(admin_id,))

    result = await tool.ainvoke({})

    pids_in_rows: set[str] = set()
    for row in result["rows"]:
        ref = row.get("fhir_ref") or ""
        if ref.startswith("Patient/"):
            pids_in_rows.add(ref.removeprefix("Patient/"))
    assert pids_in_rows == set(CARE_TEAM_PANEL)


async def test_run_panel_triage_returns_empty_envelope_for_empty_panel() -> None:
    """Unbound user → list_panel returns []. Composite returns ok: True
    with zero rows so the LLM can say 'no patients' rather than refuse."""
    tool = _tool()  # no user bound

    result = await tool.ainvoke({})

    assert result["ok"] is True
    assert result["rows"] == []
    assert result["error"] is None
    assert "CareTeam (panel)" in result["sources_checked"]


# ---------------------------------------------------------------------------
# Parallel fan-out
# ---------------------------------------------------------------------------


async def test_run_panel_triage_runs_fanout_in_parallel() -> None:
    """The outer per-pid fan-out runs concurrently, not serially.

    What this test catches: a regression that swapped the outer
    ``asyncio.gather(*[_per_pid(...)])`` for a serial loop. The
    constituent ``get_change_signal`` has its own sequential channel
    loop (4 x 50ms = 200ms per pid) and ``list_panel`` does sequential
    per-pid Patient reads (50ms each), so the absolute wall-clock floor
    is dominated by those — but the *delta* between parallel and serial
    panel-triage is still 3x the per-pid cost.

    Numbers for dr_smith's 3-patient panel:

    - list_panel: 1 CareTeam search + 3 x (Patient read + Encounter
      search) = ~350ms
    - per pid: max(change_signal serial 200ms, demographics 50ms,
      problems 50ms) = ~200ms; three pids in parallel → ~200ms;
      three pids serial → ~600ms
    - parallel total: ~550ms; fully-serial outer total: ~950ms+

    Ceiling at 0.90s gives jitter headroom while still catching the
    serial regression (which clocks at 0.95s+ on this fixture set).
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
        result = await tool.ainvoke({})
        elapsed = time.monotonic() - started

    assert result["ok"] is True
    assert elapsed < 0.90, (
        f"per-pid fan-out ran serially: elapsed={elapsed:.3f}s "
        f"(expected ~0.55s parallel across pids; serial would be ~0.95s+)"
    )


# ---------------------------------------------------------------------------
# Gate enforcement (per nested call, not just at entry)
# ---------------------------------------------------------------------------


async def test_run_panel_triage_enforces_gate_per_nested_call() -> None:
    """Every per-pid nested call goes through ``CareTeamGate.assert_authorized``.

    Defense in depth: ``list_panel`` is intrinsically CareTeam-bounded
    so the gate would never deny one of its outputs, but the per-call
    gating catches a buggy refactor that widened ``list_panel`` to
    return out-of-team rows. We patch the gate to count invocations.
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
    # 3 patients x 3 per-pid branches = 9 gate consultations minimum.
    assert call_count >= len(DR_SMITH_PANEL) * 3, (
        f"gate was consulted only {call_count} times — expected at least "
        f"{len(DR_SMITH_PANEL) * 3} (3 branches x 3 pids on dr_smith's panel)"
    )


# ---------------------------------------------------------------------------
# Schema discovery (LLM-facing description and arg shape)
# ---------------------------------------------------------------------------


async def test_run_panel_triage_is_registered_in_make_tools() -> None:
    """The tool is bound to the LLM via make_tools.

    AC: ``run_panel_triage()`` is a ``StructuredTool`` registered in
    ``make_tools(settings)``.
    """
    tools = make_tools(_settings())
    by_name = {t.name: t for t in tools}
    assert "run_panel_triage" in by_name
    tool = by_name["run_panel_triage"]
    schema = tool.args_schema.model_json_schema()
    # ``hours`` is the only argument and it's optional.
    properties = schema.get("properties", {})
    assert "patient_id" not in properties
    assert "hours" in properties
    # ``hours`` should not appear in required so the LLM can call with
    # zero arguments.
    assert "hours" not in (schema.get("required") or [])


async def test_run_panel_triage_description_signals_triage_intent() -> None:
    """The tool's description must signal when to prefer it over the granular
    panel walk — otherwise the LLM has no way to pick correctly."""
    tool = _tool()
    description = tool.description.lower()
    assert "panel" in description
    assert "triage" in description or "prioritization" in description or "first" in description
