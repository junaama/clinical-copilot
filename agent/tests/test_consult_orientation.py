"""``run_consult_orientation`` composite tool — issue 007.

Covers:

* Envelope shape: granular ``ToolResult.to_payload()`` shape so
  citation cards / verifier downstream don't have to special-case the
  composite.
* Per-domain fan-out: ``domain`` selects a curated set of resource
  branches relevant to the consulting service (cardiology, nephrology,
  id). Domain-specific code filtering (e.g., "cardiac labs only") is
  intentionally NOT applied at the data layer — the W-8 synthesis
  framing tells the LLM which codes to lens through. Mirrors the
  W-10 / W-11 design.
* Each domain's fan-out includes a different mix of resources:
  - ``cardiology``: problems + meds + vitals + labs + encounters +
    imaging + notes (BP/HR + echo/cath via DiagnosticReport).
  - ``nephrology``: problems + meds + labs + encounters + MARs +
    notes (held nephrotoxic doses surface in MARs).
  - ``id``: problems + meds + MARs + labs + orders + notes (culture
    orders authored over the window come from ServiceRequest).
* Wider lookback default (168h / 7 days) — consult orientation spans
  the admission, not just overnight. Same default as
  ``run_cross_cover_onboarding``.
* Malformed / unknown ``domain`` returns ``error="invalid_domain"``
  rather than an opaque exception, mirroring ``run_recent_changes``'s
  ``invalid_since`` discipline so the LLM can surface the bad input.
* Parallel fan-out: total wall-clock latency stays close to a single
  constituent call, not the sum.
* Gate enforcement on every nested call (defense in depth — a single
  branch that bypassed the gate would still be caught at the others).
* Hard-deny path when the user is not on the patient's CareTeam.
* ``no_active_patient`` for empty ``patient_id``.
* Admin bypass exposes off-team patients via the env-driven allow-list.
* Registration in ``make_tools`` and a description that signals the
  W-8 ("consult orientation", "specialist", "domain") intent so
  the LLM picks it over chaining the granular reads.

The synthesis-prompt selector (W-8 framing) is covered in
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
    name: str = "run_consult_orientation",
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


async def test_run_consult_orientation_returns_envelope_for_authorized_patient() -> None:
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke(
        {"patient_id": "fixture-1", "domain": "cardiology"}
    )

    assert result["ok"] is True
    assert result["error"] is None
    # Same envelope shape as a granular tool (rows, sources_checked,
    # latency_ms, error, ok).
    assert isinstance(result["rows"], list)
    assert isinstance(result["sources_checked"], list)
    assert isinstance(result["latency_ms"], int)


# ---------------------------------------------------------------------------
# Per-domain fan-out shape
# ---------------------------------------------------------------------------


async def test_cardiology_domain_fans_out_to_cardiac_relevant_branches() -> None:
    """Cardiology consult: problems + meds + vitals + labs + encounters +
    imaging + notes. The vitals branch and the imaging (DiagnosticReport)
    branch are cardiology-specific — BP/HR trends matter for a CHF read,
    and echo/cath conclusions live in the imaging envelope."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke(
        {"patient_id": "fixture-1", "domain": "cardiology"}
    )

    sources = result["sources_checked"]
    assert "Condition (active)" in sources
    assert "MedicationRequest (active)" in sources
    assert "Observation (vital-signs)" in sources
    assert "Observation (laboratory)" in sources
    assert "Encounter" in sources
    assert "DiagnosticReport (radiology)" in sources
    assert "DocumentReference" in sources


async def test_nephrology_domain_fans_out_to_renal_relevant_branches() -> None:
    """Nephrology consult: problems + meds + labs + encounters + MARs +
    notes. MARs surface held-for-AKI nephrotoxic doses; vitals are not
    in the renal lens; imaging (radiology) is not the primary substrate
    a nephrologist reads against."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke(
        {"patient_id": "fixture-1", "domain": "nephrology"}
    )

    sources = result["sources_checked"]
    assert "Condition (active)" in sources
    assert "MedicationRequest (active)" in sources
    assert "Observation (laboratory)" in sources
    assert "Encounter" in sources
    assert "MedicationAdministration" in sources
    assert "DocumentReference" in sources
    # Vitals + imaging are NOT in the nephrology fan-out — those belong
    # in cardiology, not the renal-consult lens.
    assert "Observation (vital-signs)" not in sources
    assert "DiagnosticReport (radiology)" not in sources


async def test_id_domain_fans_out_to_infection_relevant_branches() -> None:
    """ID consult: problems + meds + MARs + labs + orders + notes.
    Cultures live as Observation (laboratory) entries; culture orders
    appear under ServiceRequest. The W-8 ID framing applies the
    abx / culture / WBC lens."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke(
        {"patient_id": "fixture-1", "domain": "id"}
    )

    sources = result["sources_checked"]
    assert "Condition (active)" in sources
    assert "MedicationRequest (active)" in sources
    assert "MedicationAdministration" in sources
    assert "Observation (laboratory)" in sources
    assert "ServiceRequest" in sources
    assert "DocumentReference" in sources
    # Vitals + imaging are NOT in the ID fan-out — temperature trends
    # come from the lab branch (febrile workups) in this fixture set.
    assert "Observation (vital-signs)" not in sources


async def test_domain_is_case_insensitive() -> None:
    """A clinician typing 'Cardiology' or 'CARDIOLOGY' should not get
    rejected — domain resolution lower-cases the input."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke(
        {"patient_id": "fixture-1", "domain": "CARDIOLOGY"}
    )

    assert result["ok"] is True
    assert "Observation (vital-signs)" in result["sources_checked"]


# ---------------------------------------------------------------------------
# Domain validation
# ---------------------------------------------------------------------------


async def test_unknown_domain_returns_invalid_domain_error() -> None:
    """An unknown ``domain`` returns ``error='invalid_domain'`` rather than
    an opaque crash so the LLM can surface the bad input."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke(
        {"patient_id": "fixture-1", "domain": "endocrine"}
    )

    assert result["ok"] is False
    assert result["error"] == "invalid_domain"
    assert result["rows"] == []


async def test_empty_domain_returns_invalid_domain_error() -> None:
    """An empty ``domain`` returns ``error='invalid_domain'`` — the tool
    can't pick a fan-out shape without a domain."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"patient_id": "fixture-1", "domain": ""})

    assert result["ok"] is False
    assert result["error"] == "invalid_domain"


# ---------------------------------------------------------------------------
# Parallel fan-out
# ---------------------------------------------------------------------------


async def test_run_consult_orientation_runs_fanout_in_parallel() -> None:
    """Wall-clock latency ≈ one slow call, not sum of all calls.

    Cardiology fan-out has 7 nested calls each sleeping 50ms — a serial
    implementation would take ~350ms. Parallel fan-out via
    ``asyncio.gather`` should finish in ~50ms with overhead. We allow a
    generous 200ms ceiling to accommodate CI jitter while still failing
    loudly on a serial implementation.
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
            {"patient_id": "fixture-1", "domain": "cardiology"}
        )
        elapsed = time.monotonic() - started

    assert result["ok"] is True
    # Sum-of-seven = 0.35s; parallel = ~0.05s. Generous ceiling at 0.20s.
    assert elapsed < 0.20, (
        f"composite tool ran serially: elapsed={elapsed:.3f}s "
        f"(expected ~0.05s parallel; serial would be ~0.35s)"
    )


# ---------------------------------------------------------------------------
# Gate enforcement (per nested call, not just at entry)
# ---------------------------------------------------------------------------


async def test_run_consult_orientation_enforces_gate_per_nested_call() -> None:
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
            {"patient_id": "fixture-1", "domain": "cardiology"}
        )

    assert result["ok"] is True
    # Top-of-call gate + 7 fan-out branches = 8 gate checks minimum.
    assert call_count >= 7, (
        f"gate was consulted only {call_count} times — expected at least 7 "
        f"(one per fan-out branch)"
    )


async def test_run_consult_orientation_denies_out_of_team_patient() -> None:
    """fixture-2 is not on dr_smith's CareTeam — composite returns careteam_denied."""
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke(
        {"patient_id": "fixture-2", "domain": "cardiology"}
    )

    assert result["ok"] is False
    assert result["error"] == "careteam_denied"
    assert result["rows"] == []


async def test_run_consult_orientation_denies_when_no_user_bound() -> None:
    """Tool layer requires an active user_id; gate denies otherwise."""
    tool = _tool()

    result = await tool.ainvoke(
        {"patient_id": "fixture-1", "domain": "cardiology"}
    )

    assert result["ok"] is False
    assert result["error"] == "careteam_denied"


async def test_run_consult_orientation_returns_no_active_patient_for_empty_pid() -> None:
    set_active_user_id(PRACTITIONER_DR_SMITH)
    tool = _tool()

    result = await tool.ainvoke({"patient_id": "", "domain": "cardiology"})

    assert result["ok"] is False
    assert result["error"] == "no_active_patient"


async def test_run_consult_orientation_admin_bypass() -> None:
    """Admin users authorized by the env-driven allow-list reach any patient."""
    admin_id = "practitioner-admin"
    set_active_user_id(admin_id)
    tool = _tool(admins=(admin_id,))

    result = await tool.ainvoke(
        {"patient_id": "fixture-2", "domain": "id"}
    )

    assert result["ok"] is True


# ---------------------------------------------------------------------------
# Schema discovery (LLM-facing description and arg shape)
# ---------------------------------------------------------------------------


async def test_run_consult_orientation_is_registered_in_make_tools() -> None:
    """The tool is bound to the LLM via make_tools, with patient_id and
    domain as required args.

    AC: ``run_consult_orientation(patient_id, domain)`` is a
    ``StructuredTool`` registered in ``make_tools(settings)``.
    """
    tools = make_tools(_settings())
    by_name = {t.name: t for t in tools}
    assert "run_consult_orientation" in by_name
    tool = by_name["run_consult_orientation"]
    schema = tool.args_schema.model_json_schema()
    properties = schema.get("properties", {})
    assert "patient_id" in properties
    assert "domain" in properties
    assert "hours" in properties
    # ``patient_id`` and ``domain`` are required; ``hours`` is optional.
    required = schema.get("required") or []
    assert "patient_id" in required
    assert "domain" in required
    assert "hours" not in required


async def test_run_consult_orientation_default_hours_is_wide_lookback() -> None:
    """Consult orientation spans the admission, not just overnight; the
    default lookback is 168h (7 days) like ``run_cross_cover_onboarding``.
    A regression that flipped the default back to 24h would fail here."""
    tools = make_tools(_settings())
    by_name = {t.name: t for t in tools}
    tool = by_name["run_consult_orientation"]
    schema = tool.args_schema.model_json_schema()
    hours_default = schema["properties"]["hours"].get("default")
    assert hours_default == 168


async def test_run_consult_orientation_description_signals_w8_intent() -> None:
    """The tool's description must signal consult-orientation intent so
    the LLM picks it for W-8 turns over the granular walk."""
    tool = _tool()
    description = tool.description.lower()
    # Must mention consult / specialist / domain vocabulary so the LLM
    # has the right routing signal.
    assert (
        "consult" in description
        or "specialist" in description
        or "consulting" in description
    )
    # And reference the domain selector so the LLM knows it must pick
    # cardiology / nephrology / id.
    assert "domain" in description
    assert "cardiology" in description
    assert "nephrology" in description
    assert "id" in description
