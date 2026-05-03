"""Tests for the CareTeam authorization gate (issue 002).

The gate replaces ``_enforce_patient_context`` (the EHR-launch-era one-patient-
per-conversation pin) with CareTeam-membership checks. Tools call the gate
before issuing any FHIR query; the gate returns one of four ``AuthDecision``
values. Admin users bypass via a documented config-driven allow-list, mirroring
the PRD's "week-1 backdoor" for debugging and demos.

These tests are the contract:

- in-team patient → ``ALLOWED``
- out-of-team patient → ``CARETEAM_DENIED``
- empty patient_id → ``NO_ACTIVE_PATIENT``
- admin user → ``ALLOWED`` for any patient (via allow-list)
- ``list_panel`` is the read-side: returns subset for non-admin, full set
  for admin

Replaces the prior ``test_patient_context_guard.py`` semantics: that file
tested the SMART-pinning invariant which is removed in this slice.
"""

from __future__ import annotations

from copilot.care_team import AuthDecision, CareTeamGate, ResolvedPatient
from copilot.config import Settings
from copilot.fhir import FhirClient
from copilot.fixtures import (
    DR_SMITH_PANEL,
    PRACTITIONER_ADMIN,
    PRACTITIONER_DR_SMITH,
)


def _settings() -> Settings:
    return Settings(LLM_PROVIDER="openai", OPENAI_API_KEY="test", USE_FIXTURE_FHIR=True)


def _gate(*, admins: tuple[str, ...] = ()) -> CareTeamGate:
    return CareTeamGate(
        FhirClient(_settings()),
        admin_user_ids=frozenset(admins),
    )


# ---------------------------------------------------------------------------
# assert_authorized
# ---------------------------------------------------------------------------


async def test_assert_authorized_allows_in_team_patient() -> None:
    gate = _gate()
    decision = await gate.assert_authorized(PRACTITIONER_DR_SMITH, "fixture-1")
    assert decision is AuthDecision.ALLOWED


async def test_assert_authorized_denies_out_of_team_patient() -> None:
    gate = _gate()
    # fixture-2 (Maya Singh) is NOT in dr_smith's care team per fixtures.
    decision = await gate.assert_authorized(PRACTITIONER_DR_SMITH, "fixture-2")
    assert decision is AuthDecision.CARETEAM_DENIED


async def test_assert_authorized_empty_patient_id_returns_no_active_patient() -> None:
    gate = _gate()
    decision = await gate.assert_authorized(PRACTITIONER_DR_SMITH, "")
    assert decision is AuthDecision.NO_ACTIVE_PATIENT


async def test_assert_authorized_empty_user_id_denies() -> None:
    """An empty user_id with a real patient_id is denied — not bypassed."""
    gate = _gate()
    decision = await gate.assert_authorized("", "fixture-1")
    assert decision is AuthDecision.CARETEAM_DENIED


async def test_assert_authorized_admin_bypass_returns_allowed() -> None:
    gate = _gate(admins=(PRACTITIONER_ADMIN,))
    # Admin can reach a patient that has NO CareTeam row at all.
    decision = await gate.assert_authorized(PRACTITIONER_ADMIN, "fixture-2")
    assert decision is AuthDecision.ALLOWED


async def test_assert_authorized_admin_bypass_still_requires_patient_id() -> None:
    """Admin bypass doesn't override the no-active-patient check — that's a
    'we don't know who to look up,' not an authorization decision."""
    gate = _gate(admins=(PRACTITIONER_ADMIN,))
    decision = await gate.assert_authorized(PRACTITIONER_ADMIN, "")
    assert decision is AuthDecision.NO_ACTIVE_PATIENT


# ---------------------------------------------------------------------------
# list_panel
# ---------------------------------------------------------------------------


async def test_list_panel_returns_dr_smith_subset() -> None:
    gate = _gate()
    panel = await gate.list_panel(PRACTITIONER_DR_SMITH)
    pids = sorted(p.patient_id for p in panel)
    assert pids == sorted(DR_SMITH_PANEL)


async def test_list_panel_returns_full_set_for_admin() -> None:
    gate = _gate(admins=(PRACTITIONER_ADMIN,))
    panel = await gate.list_panel(PRACTITIONER_ADMIN)
    pids = sorted(p.patient_id for p in panel)
    # Five fixture patients in total.
    assert pids == ["fixture-1", "fixture-2", "fixture-3", "fixture-4", "fixture-5"]


async def test_list_panel_includes_demographics() -> None:
    gate = _gate()
    panel = await gate.list_panel(PRACTITIONER_DR_SMITH)
    eduardo = next(p for p in panel if p.patient_id == "fixture-1")
    assert isinstance(eduardo, ResolvedPatient)
    assert eduardo.given_name == "Eduardo"
    assert eduardo.family_name == "Perez"
    assert eduardo.birth_date == "1958-03-12"


async def test_list_panel_includes_last_admission_when_available() -> None:
    """fixture-1 has an Encounter row; the gate surfaces its period.start."""
    gate = _gate()
    panel = await gate.list_panel(PRACTITIONER_DR_SMITH)
    eduardo = next(p for p in panel if p.patient_id == "fixture-1")
    assert eduardo.last_admission is not None
    # Encounter period.start is an ISO timestamp.
    assert "T" in eduardo.last_admission


async def test_list_panel_empty_user_id_returns_empty() -> None:
    gate = _gate()
    panel = await gate.list_panel("")
    assert panel == []


async def test_list_panel_unknown_user_returns_empty() -> None:
    """A practitioner with no CareTeam membership at all gets an empty panel."""
    gate = _gate()
    panel = await gate.list_panel("practitioner-unknown")
    assert panel == []


# ---------------------------------------------------------------------------
# Regression: gate must not call ``CareTeam?participant=`` (unsupported by
# OpenEMR's FHIR module). Search shape moved to ``patient`` + ``status``
# for assert_authorized and ``status`` only for list_panel.
# ---------------------------------------------------------------------------


class _RecordingFhirClient:
    """Minimal FhirClient stand-in that records search params for assertion."""

    def __init__(self, careteams: list[dict]) -> None:
        self._careteams = careteams
        self.search_calls: list[tuple[str, dict]] = []

    async def search(
        self, resource_type: str, params: dict
    ) -> tuple[bool, list[dict], str | None, int]:
        self.search_calls.append((resource_type, dict(params)))
        if resource_type == "CareTeam":
            return True, list(self._careteams), None, 0
        return True, [], None, 0

    async def read(
        self, resource_type: str, resource_id: str
    ) -> tuple[bool, dict | None, str | None, int]:
        return False, None, None, 0


async def test_assert_authorized_queries_by_patient_not_participant() -> None:
    """Pivot the FHIR search to a parameter OpenEMR actually supports."""
    teams = [
        {
            "subject": {"reference": "Patient/p1"},
            "participant": [
                {"member": {"reference": "Practitioner/dr_smith_uuid"}}
            ],
        }
    ]
    client = _RecordingFhirClient(teams)
    gate = CareTeamGate(client)  # type: ignore[arg-type]

    decision = await gate.assert_authorized("dr_smith_uuid", "p1")
    assert decision is AuthDecision.ALLOWED

    assert client.search_calls, "gate must hit FHIR"
    resource, params = client.search_calls[0]
    assert resource == "CareTeam"
    assert params == {"patient": "p1", "status": "active"}
    assert "participant" not in params


async def test_panel_pids_for_does_not_send_participant_param() -> None:
    """``list_panel`` must client-side filter rather than rely on ``participant``."""
    teams = [
        {
            "subject": {"reference": "Patient/p1"},
            "participant": [
                {"member": {"reference": "Practitioner/dr_smith_uuid"}}
            ],
        },
        {
            "subject": {"reference": "Patient/p2"},
            "participant": [
                {"member": {"reference": "Practitioner/other_practitioner"}}
            ],
        },
    ]
    client = _RecordingFhirClient(teams)
    gate = CareTeamGate(client)  # type: ignore[arg-type]

    pids = await gate._panel_pids_for("dr_smith_uuid")

    assert pids == ["p1"]
    assert client.search_calls
    resource, params = client.search_calls[0]
    assert resource == "CareTeam"
    assert "participant" not in params
    assert params.get("status") == "active"
