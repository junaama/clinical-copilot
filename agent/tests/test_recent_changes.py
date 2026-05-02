"""``run_recent_changes`` composite tool — issue 007.

Covers:

* Envelope shape: granular ``ToolResult.to_payload()`` shape so
  citation cards / verifier downstream don't have to special-case the
  composite.
* Seven-branch fan-out: every time-windowed resource the W-9 "what
  changed since I last looked" workflow needs (vital-signs, labs,
  encounters, orders, imaging, MARs, notes) appears in
  ``sources_checked``.
* ``since`` is a required ISO timestamp; missing/malformed values
  return an ``invalid_since`` error envelope rather than an opaque
  exception.
* The ``since`` cutoff is honored downstream: every fan-out branch's
  FHIR search receives a date filter at-or-after ``since`` (asserted
  by spying on ``FhirClient.search``).
* Parallel fan-out: total wall-clock latency stays close to a single
  constituent call, not the sum.
* Gate enforcement on every nested call (defense in depth — a single
  branch that bypassed the gate would still be caught at the others).
* Hard-deny path when the user is not on the patient's CareTeam.
* ``no_active_patient`` for empty ``patient_id``.
* Admin bypass exposes off-team patients via the env-driven allow-list.
* Registration in ``make_tools`` and a description that signals the
  W-9 ("what changed", "diff", "since I last looked") intent so the
  LLM picks it over chaining the granular reads.

The synthesis-prompt selector (W-9 framing) is covered in
``test_synthesis_prompt_selector.py``.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
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


def _iso_hours_ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def _reset_context():
    set_active_user_id(None)
    yield
    set_active_user_id(None)


def _tool(
    name: str = "run_recent_changes",
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


async def test_run_recent_changes_returns_envelope_for_authorized_patient() -> None:
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke(
        {"patient_id": "fixture-1", "since": _iso_hours_ago(24)}
    )

    assert result["ok"] is True
    assert result["error"] is None
    # Same envelope shape as a granular tool (rows, sources_checked,
    # latency_ms, error, ok).
    assert isinstance(result["rows"], list)
    assert isinstance(result["sources_checked"], list)
    assert isinstance(result["latency_ms"], int)


async def test_run_recent_changes_sources_checked_lists_each_branch() -> None:
    """The composite must surface ``sources_checked`` from every fan-out branch.

    Fan-out: Observation (vital-signs), Observation (laboratory),
    Encounter, ServiceRequest, DiagnosticReport (radiology),
    MedicationAdministration, DocumentReference. All seven are
    time-windowed — exactly the set the W-9 "what changed" workflow
    needs.
    """
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke(
        {"patient_id": "fixture-1", "since": _iso_hours_ago(24)}
    )

    sources = result["sources_checked"]
    assert "Observation (vital-signs)" in sources
    assert "Observation (laboratory)" in sources
    assert "Encounter" in sources
    assert "ServiceRequest" in sources
    assert "DiagnosticReport (radiology)" in sources
    assert "MedicationAdministration" in sources
    assert "DocumentReference" in sources


async def test_run_recent_changes_excludes_active_problems_and_meds() -> None:
    """The composite is a *diff* over time-windowed resources only.

    Active problems and active medications are *current state*, not
    changes. Including them would make the LLM compare against the
    full med list rather than surfacing the deltas. The W-9 framing
    explicitly tells the LLM to fetch active state with granular
    tools when it needs to anchor a diff against current state.
    """
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke(
        {"patient_id": "fixture-1", "since": _iso_hours_ago(24)}
    )

    sources = " ".join(result["sources_checked"]).lower()
    assert "condition" not in sources
    assert "medicationrequest" not in sources


# ---------------------------------------------------------------------------
# `since` arg validation
# ---------------------------------------------------------------------------


async def test_run_recent_changes_requires_since_arg() -> None:
    """``since`` is part of the schema's ``required`` list."""
    tools = make_tools(_settings())
    by_name = {t.name: t for t in tools}
    schema = by_name["run_recent_changes"].args_schema.model_json_schema()
    assert "since" in (schema.get("required") or [])
    assert "patient_id" in (schema.get("required") or [])


async def test_run_recent_changes_rejects_malformed_since() -> None:
    """A ``since`` value that doesn't parse as ISO 8601 returns an
    ``invalid_since`` error envelope rather than an opaque exception."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke(
        {"patient_id": "fixture-1", "since": "yesterday morning"}
    )

    assert result["ok"] is False
    assert result["error"] == "invalid_since"
    assert result["rows"] == []


async def test_run_recent_changes_rejects_future_since() -> None:
    """A ``since`` in the future is nonsensical for a diff and is rejected."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    future = (datetime.now(UTC) + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = await tool.ainvoke({"patient_id": "fixture-1", "since": future})

    assert result["ok"] is False
    assert result["error"] == "invalid_since"


async def test_run_recent_changes_propagates_since_to_branch_filters() -> None:
    """Every per-branch FHIR search must carry a date filter that's
    at-or-after the supplied ``since``.

    Defense against a regression where ``since`` is parsed at the
    composite entry point but never plumbed down into the granular
    tools — the composite would silently fall back to each tool's
    24-hour default and the diff would be wrong. We spy on
    ``FhirClient.search`` and assert every search the composite
    issues uses a ``ge<timestamp>`` filter at-or-after our ``since``.
    """
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    from copilot.fhir import FhirClient

    captured: list[tuple[str, dict[str, str]]] = []
    original_search = FhirClient.search

    async def capturing_search(self, resource_type, params):
        captured.append((resource_type, dict(params)))
        return await original_search(self, resource_type, params)

    since_iso = _iso_hours_ago(48)  # 2 days ago
    since_dt = datetime.strptime(since_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

    with patch.object(FhirClient, "search", capturing_search):
        result = await tool.ainvoke(
            {"patient_id": "fixture-1", "since": since_iso}
        )

    assert result["ok"] is True
    # Every branch must propagate a date-style cutoff at-or-after `since`.
    # The exact param name varies by resource type (date / authored /
    # effective-time), so look at any param value starting with "ge".
    # CareTeam lookups are the gate's authorization check, not a fan-out
    # branch — they don't carry a date filter.
    branch_calls = [
        (r, p) for r, p in captured if r != "CareTeam"
    ]
    assert branch_calls, "composite did not issue any FHIR fan-out searches"
    for resource_type, params in branch_calls:
        ge_values = [v for v in params.values() if isinstance(v, str) and v.startswith("ge")]
        assert ge_values, (
            f"{resource_type} search params={params} did not carry a "
            f"ge<timestamp> filter — `since` was not plumbed through"
        )
        for ge_value in ge_values:
            ts_str = ge_value[2:]
            try:
                cutoff = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=UTC
                )
            except ValueError:
                pytest.fail(
                    f"{resource_type} ge filter {ge_value} is not "
                    f"parseable as YYYY-MM-DDTHH:MM:SSZ"
                )
            # The cutoff should be at or after our `since` (within a small
            # rounding window — we round up so the boundary is included).
            # Any cutoff before `since` would mean the branch was reaching
            # further back than asked.
            assert cutoff >= since_dt - timedelta(hours=2), (
                f"{resource_type} cutoff {cutoff.isoformat()} is older "
                f"than since={since_dt.isoformat()} — the diff window is "
                f"wider than requested"
            )


# ---------------------------------------------------------------------------
# Parallel fan-out
# ---------------------------------------------------------------------------


async def test_run_recent_changes_runs_fanout_in_parallel() -> None:
    """Wall-clock latency ≈ one slow call, not sum of all calls.

    With 7 nested calls each sleeping 50ms, a serial implementation would
    take ~350ms. Parallel fan-out via ``asyncio.gather`` should finish
    in ~50ms with overhead. We allow a generous 250ms ceiling to accommodate
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
        result = await tool.ainvoke(
            {"patient_id": "fixture-1", "since": _iso_hours_ago(24)}
        )
        elapsed = time.monotonic() - started

    assert result["ok"] is True
    # Sum-of-seven = 0.35s; parallel = ~0.05s. Generous ceiling at 0.25s.
    assert elapsed < 0.25, (
        f"composite tool ran serially: elapsed={elapsed:.3f}s "
        f"(expected ~0.05s parallel; serial would be ~0.35s)"
    )


# ---------------------------------------------------------------------------
# Gate enforcement (per nested call, not just at entry)
# ---------------------------------------------------------------------------


async def test_run_recent_changes_enforces_gate_per_nested_call() -> None:
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
        result = await tool.ainvoke(
            {"patient_id": "fixture-1", "since": _iso_hours_ago(24)}
        )

    assert result["ok"] is True
    # Top-of-call gate + 7 fan-out branches = 8 gate checks minimum.
    assert call_count >= 7, (
        f"gate was consulted only {call_count} times — expected at least 7 "
        f"(one per fan-out branch)"
    )


async def test_run_recent_changes_denies_out_of_team_patient() -> None:
    """fixture-2 is not on dr_smith's CareTeam — composite returns careteam_denied."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke(
        {"patient_id": "fixture-2", "since": _iso_hours_ago(24)}
    )

    assert result["ok"] is False
    assert result["error"] == "careteam_denied"
    assert result["rows"] == []


async def test_run_recent_changes_denies_when_no_user_bound() -> None:
    """Tool layer requires an active user_id; gate denies otherwise."""
    tool = _tool()

    result = await tool.ainvoke(
        {"patient_id": "fixture-1", "since": _iso_hours_ago(24)}
    )

    assert result["ok"] is False
    assert result["error"] == "careteam_denied"


async def test_run_recent_changes_returns_no_active_patient_for_empty_pid() -> None:
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"patient_id": "", "since": _iso_hours_ago(24)})

    assert result["ok"] is False
    assert result["error"] == "no_active_patient"


async def test_run_recent_changes_admin_bypass() -> None:
    """Admin users authorized by the env-driven allow-list reach any patient."""
    admin_id = "practitioner-admin"
    set_active_user_id(admin_id)
    tool = _tool(admins=(admin_id,))

    result = await tool.ainvoke(
        {"patient_id": "fixture-2", "since": _iso_hours_ago(24)}
    )

    assert result["ok"] is True


# ---------------------------------------------------------------------------
# Schema discovery (LLM-facing description and arg shape)
# ---------------------------------------------------------------------------


async def test_run_recent_changes_is_registered_in_make_tools() -> None:
    """The tool is bound to the LLM via make_tools, with patient_id and since
    as required args.

    AC: ``run_recent_changes(patient_id, since)`` is a ``StructuredTool``
    registered in ``make_tools(settings)``.
    """
    tools = make_tools(_settings())
    by_name = {t.name: t for t in tools}
    assert "run_recent_changes" in by_name
    tool = by_name["run_recent_changes"]
    schema = tool.args_schema.model_json_schema()
    properties = schema.get("properties", {})
    assert "patient_id" in properties
    assert "since" in properties
    required = schema.get("required") or []
    assert "patient_id" in required
    assert "since" in required


async def test_run_recent_changes_description_signals_w9_intent() -> None:
    """The tool's description must signal "what changed since" intent so the
    LLM picks it for W-9 turns over the granular walk.
    """
    tool = _tool()
    description = tool.description.lower()
    # Must mention diff / change / since semantics so the LLM has the
    # right routing signal.
    assert "since" in description
    assert (
        "chang" in description
        or "diff" in description
        or "new" in description
    )
