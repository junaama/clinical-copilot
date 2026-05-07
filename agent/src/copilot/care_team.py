"""CareTeam authorization gate (issue 002).

Replaces the EHR-launch-era ``_enforce_patient_context`` pin (which compared a
tool's ``patient_id`` to whichever patient the SMART launch had bound) with a
membership check against the user's CareTeam roster. Every patient-data tool
runs ``assert_authorized`` before issuing a FHIR query; the read-side
``list_panel`` powers the empty-state Panel UI in the standalone shell.

Design notes:

- The gate takes a ``FhirClient`` so fixture mode and real OpenEMR share one
  code path. ``_fixture_search`` knows how to filter ``CareTeam`` by
  ``participant`` (added alongside this module).
- Admin bypass is a frozenset of practitioner ids injected at construction.
  This is the deliberate week-1 backdoor described in the PRD: in production
  the source of truth for "is admin" is OpenEMR's ACL framework, but plumbing
  a per-call ACL round-trip from Python is heavyweight and out of scope for
  this slice. The env-driven allow-list keeps the demo behavior visible
  (admin sees everyone, dr_smith sees a subset) without that round-trip.
- ``AuthDecision`` is a closed enum with four values per PRD; a denial is
  rendered to the LLM as ``error="<value>"`` so prompts can react explicitly.
- ``list_panel`` resolves each membership row to a ``ResolvedPatient`` with
  enough fields for the empty-state UI (name, DOB, last admission). Room/bed
  is optional and not currently surfaced by the FHIR Patient/Encounter
  shape — left ``None`` for now.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .fhir import FhirClient


class AuthDecision(StrEnum):
    """Outcome of an authorization check at the tool layer.

    Values are intentionally string-typed so they can be embedded in
    ``ToolResult.error`` and round-trip through the LLM tool-message channel
    without re-encoding.
    """

    ALLOWED = "allowed"
    CARETEAM_DENIED = "careteam_denied"
    NO_ACTIVE_PATIENT = "no_active_patient"
    PATIENT_CONTEXT_MISMATCH = "patient_context_mismatch"


@dataclass(frozen=True)
class ResolvedPatient:
    """One row of the user's CareTeam panel.

    ``last_admission`` and ``room`` are optional because the FHIR resource
    shapes don't always carry them. The Panel UI handles ``None`` gracefully.
    """

    patient_id: str
    given_name: str
    family_name: str
    birth_date: str
    last_admission: str | None = None
    room: str | None = None


def _first_given_name(patient: dict) -> str:
    name_obj = (patient.get("name") or [{}])[0]
    given = name_obj.get("given") or []
    return given[0] if given else ""


def _family_name(patient: dict) -> str:
    name_obj = (patient.get("name") or [{}])[0]
    return name_obj.get("family") or ""


def _team_has_practitioner(team: dict, practitioner_ref: str) -> bool:
    """True iff ``team.participant[].member.reference`` matches ``practitioner_ref``."""
    for p in team.get("participant") or []:
        if ((p.get("member") or {}).get("reference") or "") == practitioner_ref:
            return True
    return False


class CareTeamGate:
    """Authorization gate scoped to one user's CareTeam roster."""

    def __init__(
        self,
        client: FhirClient,
        *,
        admin_user_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._client = client
        self._admin_user_ids = admin_user_ids

    def is_admin(self, user_id: str) -> bool:
        return bool(user_id) and user_id in self._admin_user_ids

    async def assert_authorized(
        self, user_id: str, patient_id: str
    ) -> AuthDecision:
        """Return the gate's decision for a (user, patient) pair.

        Order of checks matters: ``no_active_patient`` is reported first so
        the caller can distinguish "we don't know which patient to look up"
        from an authorization denial. Admin bypass applies only when a
        patient_id is present.
        """
        if not patient_id:
            return AuthDecision.NO_ACTIVE_PATIENT
        if self.is_admin(user_id):
            return AuthDecision.ALLOWED
        if not user_id:
            return AuthDecision.CARETEAM_DENIED

        # OpenEMR's ``FhirCareTeamService::loadSearchParameters`` only exposes
        # ``patient``, ``status``, ``_id``, ``_lastUpdated`` — there is no
        # ``participant`` search param. We pivot the query: search teams by
        # patient (cheap, scoped), then filter the participants client-side
        # for our user.
        ok, entries, _err, _ms = await self._client.search(
            "CareTeam",
            {"patient": patient_id, "status": "active"},
        )
        if not ok:
            # Fail closed: if we can't verify membership we deny rather
            # than leaking patient data on a transient FHIR failure.
            return AuthDecision.CARETEAM_DENIED

        practitioner_ref = f"Practitioner/{user_id}"
        for team in entries:
            if _team_has_practitioner(team, practitioner_ref):
                return AuthDecision.ALLOWED
        return AuthDecision.CARETEAM_DENIED

    async def list_panel(self, user_id: str) -> list[ResolvedPatient]:
        """Return the user's CareTeam roster, fully resolved.

        Resolution does two FHIR calls per patient (Patient read + Encounter
        search for last admission). For a 30-patient panel that is 60 calls;
        running them sequentially over Railway's internal network takes
        ~30s and trips Chrome's 45s CDP timeout when the panel boots in the
        browser. Running them concurrently with ``asyncio.gather`` drops
        wall-clock to ~1-2s.
        """
        import asyncio

        if self.is_admin(user_id):
            pids = await self._all_patient_ids()
        elif not user_id:
            return []
        else:
            pids = await self._panel_pids_for(user_id)

        async def _resolve_one(pid: str) -> ResolvedPatient | None:
            patient, last_admission = await asyncio.gather(
                self._read_patient(pid),
                self._latest_admission(pid),
            )
            if patient is None:
                return None
            return ResolvedPatient(
                patient_id=pid,
                given_name=_first_given_name(patient),
                family_name=_family_name(patient),
                birth_date=patient.get("birthDate") or "",
                last_admission=last_admission,
                room=None,
            )

        results = await asyncio.gather(*(_resolve_one(pid) for pid in pids))
        return [r for r in results if r is not None]

    async def _panel_pids_for(self, user_id: str) -> list[str]:
        # ``CareTeam?participant=`` isn't supported by OpenEMR's FHIR module
        # (see ``assert_authorized``). Fetching every active CareTeam and
        # filtering client-side scales linearly with team count; for a single
        # clinician's panel this is well within budget.
        ok, entries, _err, _ms = await self._client.search(
            "CareTeam",
            {"status": "active"},
        )
        if not ok:
            return []
        practitioner_ref = f"Practitioner/{user_id}"
        seen: set[str] = set()
        pids: list[str] = []
        for team in entries:
            if not _team_has_practitioner(team, practitioner_ref):
                continue
            ref = (team.get("subject") or {}).get("reference") or ""
            if not ref.startswith("Patient/"):
                continue
            pid = ref.removeprefix("Patient/")
            if pid in seen:
                continue
            seen.add(pid)
            pids.append(pid)
        return pids

    async def _all_patient_ids(self) -> list[str]:
        ok, entries, _err, _ms = await self._client.search("Patient", {})
        if not ok:
            return []
        return [p.get("id") or "" for p in entries if p.get("id")]

    async def _read_patient(self, patient_id: str) -> dict | None:
        ok, resource, _err, _ms = await self._client.read("Patient", patient_id)
        if not ok or resource is None:
            return None
        return resource

    async def _latest_admission(self, patient_id: str) -> str | None:
        ok, entries, _err, _ms = await self._client.search(
            "Encounter", {"patient": patient_id}
        )
        if not ok or not entries:
            return None
        starts = [
            (e.get("period") or {}).get("start")
            for e in entries
            if (e.get("period") or {}).get("start")
        ]
        if not starts:
            return None
        starts.sort(reverse=True)
        return starts[0]
