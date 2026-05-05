"""Granular FHIR-read tools — 13 single-purpose patient-data readers.

Each tool is a thin wrapper over ``FhirClient`` plus field allowlist +
absence markers. All async; all return JSON-serializable dicts so they
can be embedded in tool messages.

Convention: every patient-data tool runs through ``CareTeamGate`` before
issuing a FHIR query.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

from ..care_team import CareTeamGate
from ..config import Settings
from ..fhir import FhirClient, Row, ToolResult
from ..fixtures import CARE_TEAM_PANEL
from .helpers import (
    _condition_fields,
    _diagnostic_report_fields,
    _document_fields,
    _encounter_fields,
    _enforce_patient_authorization,
    _hours_window,
    _medication_admin_fields,
    _medication_fields,
    _observation_fields,
    _patient_demographics_fields,
    _patient_matches_name,
    _registry_to_patient_dict,
    _result_from_entries,
    _service_request_fields,
    get_active_registry,
    get_active_user_id,
)


def make_granular_tools(
    settings: Settings, client: FhirClient, gate: CareTeamGate
) -> tuple[list[StructuredTool], dict[str, Any]]:
    """Build the 13 granular tools and return them with their raw callables.

    Returns ``(structured_tools, callables)`` where ``callables`` is a dict
    mapping function names to the raw async functions so composite tools
    can invoke them directly (without going through StructuredTool).
    """

    async def resolve_patient(
        name: str,
        dob: str | None = None,
        mrn_tail: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a patient mention against the user's CareTeam roster.

        Status semantics:

        * ``resolved`` — exactly one match. ``patients`` carries the row.
        * ``ambiguous`` — multiple matches. ``patients`` carries the
          candidates with DOBs so the caller can ask the user to
          disambiguate.
        * ``not_found`` — zero matches. Privacy-correct collapse of "exists
          but not on your CareTeam" with "doesn't exist anywhere"; both
          surface the same way.
        * ``clarify`` — ``name`` is too sparse to search.

        The resolver is cache-hit-idempotent within a conversation: it
        consults the active registry contextvar first and short-circuits
        on a single-name match before paying a FHIR roundtrip.
        """
        started_name = (name or "").strip()
        if len(started_name) < 2:
            return {
                "ok": False,
                "status": "clarify",
                "patients": [],
                "message": (
                    "I need at least a partial name to look up a patient — "
                    "please tell me who you mean."
                ),
                "sources_checked": [],
                "latency_ms": 0,
            }

        name_lower = started_name.lower()
        registry = get_active_registry()

        cached_candidates = [
            entry
            for entry in registry.values()
            if _patient_matches_name(entry, name_lower)
            and (dob is None or entry.get("birth_date") == dob)
        ]
        if len(cached_candidates) == 1:
            return {
                "ok": True,
                "status": "resolved",
                "patients": [_registry_to_patient_dict(cached_candidates[0])],
                "message": "",
                "sources_checked": ["CareTeam (cached)"],
                "latency_ms": 0,
            }

        user_id = get_active_user_id() or ""
        roster = await gate.list_panel(user_id)

        candidates = []
        for resolved in roster:
            row = {
                "patient_id": resolved.patient_id,
                "given_name": resolved.given_name,
                "family_name": resolved.family_name,
                "birth_date": resolved.birth_date,
            }
            if not _patient_matches_name(row, name_lower):
                continue
            if dob is not None and row["birth_date"] != dob:
                continue
            candidates.append(row)

        if len(candidates) == 1:
            return {
                "ok": True,
                "status": "resolved",
                "patients": candidates,
                "message": "",
                "sources_checked": ["CareTeam"],
                "latency_ms": 0,
            }
        if len(candidates) > 1:
            return {
                "ok": True,
                "status": "ambiguous",
                "patients": candidates,
                "message": (
                    "Multiple patients on your CareTeam match — "
                    "please disambiguate by date of birth."
                ),
                "sources_checked": ["CareTeam"],
                "latency_ms": 0,
            }
        return {
            "ok": True,
            "status": "not_found",
            "patients": [],
            "message": (
                "No patient on your CareTeam matches that name. "
                "If you believe this patient is on your panel, double-check "
                "spelling or use a more specific identifier."
            ),
            "sources_checked": ["CareTeam"],
            "latency_ms": 0,
        }

    async def get_patient_demographics(patient_id: str) -> dict[str, Any]:
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied
        ok, resource, err, ms = await client.read("Patient", patient_id)
        entries = [resource] if resource else []
        return _result_from_entries(
            entries,
            resource_type="Patient",
            field_extractor=_patient_demographics_fields,
            sources=("Patient",),
            error=err,
            latency_ms=ms,
            ok=ok,
        ).to_payload()

    async def get_active_problems(patient_id: str) -> dict[str, Any]:
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied
        ok, entries, err, ms = await client.search(
            "Condition", {"patient": patient_id, "clinical-status": "active"}
        )
        return _result_from_entries(
            entries,
            resource_type="Condition",
            field_extractor=_condition_fields,
            sources=("Condition (active)",),
            error=err,
            latency_ms=ms,
            ok=ok,
        ).to_payload()

    async def get_active_medications(patient_id: str) -> dict[str, Any]:
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied
        ok, entries, err, ms = await client.search(
            "MedicationRequest", {"patient": patient_id, "status": "active"}
        )
        return _result_from_entries(
            entries,
            resource_type="MedicationRequest",
            field_extractor=_medication_fields,
            sources=("MedicationRequest (active)",),
            error=err,
            latency_ms=ms,
            ok=ok,
        ).to_payload()

    async def get_recent_vitals(patient_id: str, hours: int = 24) -> dict[str, Any]:
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied
        ok, entries, err, ms = await client.search(
            "Observation",
            {
                "patient": patient_id,
                "category": "vital-signs",
                "date": _hours_window(hours),
            },
        )
        return _result_from_entries(
            entries,
            resource_type="Observation",
            field_extractor=_observation_fields,
            sources=("Observation (vital-signs)",),
            error=err,
            latency_ms=ms,
            ok=ok,
            sentinel_fields=("note",),
        ).to_payload()

    async def get_recent_labs(patient_id: str, hours: int = 24) -> dict[str, Any]:
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied
        ok, entries, err, ms = await client.search(
            "Observation",
            {
                "patient": patient_id,
                "category": "laboratory",
                "date": _hours_window(hours),
            },
        )
        return _result_from_entries(
            entries,
            resource_type="Observation",
            field_extractor=_observation_fields,
            sources=("Observation (laboratory)",),
            error=err,
            latency_ms=ms,
            ok=ok,
            sentinel_fields=("note",),
        ).to_payload()

    async def get_recent_encounters(patient_id: str, hours: int = 24) -> dict[str, Any]:
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied
        ok, entries, err, ms = await client.search(
            "Encounter",
            {"patient": patient_id, "date": _hours_window(hours)},
        )
        return _result_from_entries(
            entries,
            resource_type="Encounter",
            field_extractor=_encounter_fields,
            sources=("Encounter",),
            error=err,
            latency_ms=ms,
            ok=ok,
        ).to_payload()

    async def get_my_patient_list() -> dict[str, Any]:
        """Return the user's care-team patient list as patient ids."""
        if settings.use_fixture_fhir:
            rows = tuple(
                Row(
                    fhir_ref=f"Patient/{pid}",
                    resource_type="Patient",
                    fields={"patient_id": pid},
                )
                for pid in CARE_TEAM_PANEL
            )
            return ToolResult(
                ok=True,
                rows=rows,
                sources_checked=("CareTeam (fixture panel)",),
                latency_ms=0,
            ).to_payload()

        practitioner_id = get_active_user_id()
        if not practitioner_id:
            return ToolResult(
                ok=False,
                sources_checked=(),
                error="no_practitioner_context",
                latency_ms=0,
            ).to_payload()

        ok, entries, err, ms = await client.search(
            "CareTeam",
            {"participant": f"Practitioner/{practitioner_id}"},
        )
        if not ok:
            return ToolResult(
                ok=False,
                sources_checked=("CareTeam",),
                error=err or "care_team_query_failed",
                latency_ms=ms,
            ).to_payload()

        seen: set[str] = set()
        rows_list: list[Row] = []
        for team in entries:
            ref = (team.get("subject") or {}).get("reference") or ""
            if not ref.startswith("Patient/"):
                continue
            pid = ref.removeprefix("Patient/")
            if pid in seen:
                continue
            seen.add(pid)
            rows_list.append(
                Row(
                    fhir_ref=ref,
                    resource_type="Patient",
                    fields={"patient_id": pid},
                )
            )
        return ToolResult(
            ok=True,
            rows=tuple(rows_list),
            sources_checked=("CareTeam",),
            latency_ms=ms,
        ).to_payload()

    async def get_change_signal(patient_id: str, hours: int = 24) -> dict[str, Any]:
        """Lightweight change-signal probe for one patient."""
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied
        params = {"patient": patient_id, "date": _hours_window(hours)}
        signals: list[tuple[str, dict[str, Any]]] = [
            ("vital-signs", {**params, "category": "vital-signs"}),
            ("labs", {**params, "category": "laboratory"}),
            ("encounters", params.copy()),
            ("documents", params.copy()),
        ]
        rows_list: list[Row] = []
        ok_overall = True
        last_err: str | None = None
        latency_total = 0
        for channel_name, channel_params in signals:
            resource_type = (
                "Observation"
                if channel_name in {"vital-signs", "labs"}
                else "Encounter"
                if channel_name == "encounters"
                else "DocumentReference"
            )
            ok, entries, err, ms = await client.search(resource_type, channel_params)
            latency_total += ms
            if not ok:
                ok_overall = False
                last_err = err
            rows_list.append(
                Row(
                    fhir_ref=f"{resource_type}/_summary=count?patient={patient_id}",
                    resource_type=resource_type,
                    fields={
                        "channel": channel_name,
                        "count": len(entries) if ok else 0,
                        "patient_id": patient_id,
                    },
                )
            )
        return ToolResult(
            ok=ok_overall,
            rows=tuple(rows_list),
            sources_checked=tuple(s for s, _ in signals),
            error=last_err,
            latency_ms=latency_total,
        ).to_payload()

    async def get_recent_orders(patient_id: str, hours: int = 24) -> dict[str, Any]:
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied
        ok, entries, err, ms = await client.search(
            "ServiceRequest",
            {"patient": patient_id, "authored": _hours_window(hours)},
        )
        return _result_from_entries(
            entries,
            resource_type="ServiceRequest",
            field_extractor=_service_request_fields,
            sources=("ServiceRequest",),
            error=err,
            latency_ms=ms,
            ok=ok,
        ).to_payload()

    async def get_imaging_results(patient_id: str, hours: int = 24) -> dict[str, Any]:
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied
        ok, entries, err, ms = await client.search(
            "DiagnosticReport",
            {
                "patient": patient_id,
                "category": "radiology",
                "date": _hours_window(hours),
            },
        )
        return _result_from_entries(
            entries,
            resource_type="DiagnosticReport",
            field_extractor=_diagnostic_report_fields,
            sources=("DiagnosticReport (radiology)",),
            error=err,
            latency_ms=ms,
            ok=ok,
        ).to_payload()

    async def get_medication_administrations(
        patient_id: str, hours: int = 24
    ) -> dict[str, Any]:
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied
        ok, entries, err, ms = await client.search(
            "MedicationAdministration",
            {"patient": patient_id, "effective-time": _hours_window(hours)},
        )
        return _result_from_entries(
            entries,
            resource_type="MedicationAdministration",
            field_extractor=_medication_admin_fields,
            sources=("MedicationAdministration",),
            error=err,
            latency_ms=ms,
            ok=ok,
        ).to_payload()

    async def get_clinical_notes(patient_id: str, hours: int = 24) -> dict[str, Any]:
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied
        ok, entries, err, ms = await client.search(
            "DocumentReference",
            {"patient": patient_id, "date": _hours_window(hours)},
        )
        return _result_from_entries(
            entries,
            resource_type="DocumentReference",
            field_extractor=_document_fields,
            sources=("DocumentReference",),
            error=err,
            latency_ms=ms,
            ok=ok,
            sentinel_fields=("body",),
        ).to_payload()

    # Collect raw callables for composite tools
    callables = {
        "get_patient_demographics": get_patient_demographics,
        "get_active_problems": get_active_problems,
        "get_active_medications": get_active_medications,
        "get_recent_vitals": get_recent_vitals,
        "get_recent_labs": get_recent_labs,
        "get_recent_encounters": get_recent_encounters,
        "get_change_signal": get_change_signal,
        "get_recent_orders": get_recent_orders,
        "get_imaging_results": get_imaging_results,
        "get_medication_administrations": get_medication_administrations,
        "get_clinical_notes": get_clinical_notes,
    }

    structured_tools = [
        StructuredTool.from_function(
            coroutine=resolve_patient,
            name="resolve_patient",
            description=(
                "Resolve a patient name (and optionally DOB or MRN tail) "
                "against the user's CareTeam. Call this FIRST whenever the "
                "user mentions a patient by name (e.g. 'Hayes', 'tell me "
                "about Robert'); use the returned patient_id for downstream "
                "tool calls. Status values: 'resolved' (single match — "
                "proceed), 'ambiguous' (multiple matches — ask the user to "
                "pick one by DOB), 'not_found' (no match on the user's "
                "CareTeam — say so and stop), 'clarify' (input too sparse). "
                "Subsequent mentions of the same name in the same "
                "conversation are O(1) cache hits."
            ),
        ),
        StructuredTool.from_function(
            coroutine=get_my_patient_list,
            name="get_my_patient_list",
            description=(
                "Return the user's care-team patient list as a list of patient ids. "
                "Use as the FIRST step of UC-1 triage (cross-patient ranking). "
                "No arguments."
            ),
        ),
        StructuredTool.from_function(
            coroutine=get_change_signal,
            name="get_change_signal",
            description=(
                "Lightweight change-signal probe for ONE patient. Returns counts of "
                "vitals, labs, encounters, and document refs in the lookback window "
                "(default 24h). Use during UC-1 triage Stage 1, called in parallel "
                "across the patient list. Cheap; no resource bodies returned."
            ),
        ),
        StructuredTool.from_function(
            coroutine=get_patient_demographics,
            name="get_patient_demographics",
            description=(
                "Return demographics (name, DOB, gender) for the patient. "
                "Call once per session to anchor the brief."
            ),
        ),
        StructuredTool.from_function(
            coroutine=get_active_problems,
            name="get_active_problems",
            description=(
                "Return the patient's active problem list. Use to weigh significance of "
                "overnight changes (e.g., a 2 lb gain matters for a CHF patient)."
            ),
        ),
        StructuredTool.from_function(
            coroutine=get_active_medications,
            name="get_active_medications",
            description=(
                "Return the patient's currently active medications with dose, route, "
                "frequency, and prescriber."
            ),
        ),
        StructuredTool.from_function(
            coroutine=get_recent_vitals,
            name="get_recent_vitals",
            description=(
                "Return vital-sign observations for the patient within the lookback "
                "window (default 24 hours)."
            ),
        ),
        StructuredTool.from_function(
            coroutine=get_recent_labs,
            name="get_recent_labs",
            description=(
                "Return laboratory observations for the patient within the lookback "
                "window (default 24 hours)."
            ),
        ),
        StructuredTool.from_function(
            coroutine=get_recent_encounters,
            name="get_recent_encounters",
            description=(
                "Return encounters (rapid responses, transfers, ED visits) for the "
                "patient within the lookback window (default 24 hours)."
            ),
        ),
        StructuredTool.from_function(
            coroutine=get_recent_orders,
            name="get_recent_orders",
            description=(
                "Return ServiceRequest orders authored for the patient within the "
                "lookback window (default 24h): imaging, labs, consults, etc."
            ),
        ),
        StructuredTool.from_function(
            coroutine=get_imaging_results,
            name="get_imaging_results",
            description=(
                "Return DiagnosticReport rows for the patient's radiology in the "
                "lookback window (default 24h). The conclusion text is the radiologist's "
                "impression."
            ),
        ),
        StructuredTool.from_function(
            coroutine=get_medication_administrations,
            name="get_medication_administrations",
            description=(
                "Return MedicationAdministration rows for the patient within the "
                "lookback window (default 24h). lifecycle_status is canonicalized: "
                "'given' (was administered), 'held' (not given, with reason), "
                "'in-progress', 'stopped'. Raw FHIR status is in raw_status."
            ),
        ),
        StructuredTool.from_function(
            coroutine=get_clinical_notes,
            name="get_clinical_notes",
            description=(
                "Return clinical notes (nursing progress, cross-cover physician, "
                "consultant) for the patient within the lookback window (default 24 hours). "
                "Note bodies are length-capped; consult the chart for full text."
            ),
        ),
    ]

    return structured_tools, callables
