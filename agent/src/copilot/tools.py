"""LangChain-tool-decorated functions the agent can call.

Each tool is a thin wrapper over ``FhirClient`` plus field allowlist + absence
markers (ARCHITECTURE §11, §15). All async; all return JSON-serializable dicts
so they can be embedded in tool messages.

Convention: every tool requires ``patient_id``. The active SMART session's
patient is bound via ``set_active_patient_id`` before the agent runs; any
tool call whose ``patient_id`` parameter doesn't match that bound id is hard-
refused at the tool layer with ``error='patient_context_mismatch'`` per
ARCHITECTURE.md §7 ("defense in depth — every tool call independently
validates the patient ID").
"""

from __future__ import annotations

import base64
import contextvars
from datetime import datetime, timedelta, timezone
from typing import Any

from langchain_core.tools import StructuredTool

from .config import Settings
from .fhir import ABSENT, FhirClient, Row, ToolResult
from .fixtures import CARE_TEAM_PANEL


# Contextvar carries the active SMART patient_id into the async tool calls
# without threading it through every signature. Set by the graph's agent_node
# before invoking the agent; auto-propagates to spawned asyncio tasks.
_active_patient_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "copilot_active_patient_id", default=None
)


def set_active_patient_id(patient_id: str | None) -> None:
    _active_patient_id.set(patient_id)


def get_active_patient_id() -> str | None:
    return _active_patient_id.get()


def _enforce_patient_context(patient_id: str) -> dict[str, Any] | None:
    """Hard-refuse tool calls whose ``patient_id`` doesn't match the bound
    SMART context. Returns the refusal payload, or ``None`` when allowed.

    No-op when no active context is bound (so unit tests and one-off scripts
    that don't set a context still work).
    """
    active = get_active_patient_id()
    if active is None or patient_id == active:
        return None
    return ToolResult(
        ok=False,
        sources_checked=(),
        error="patient_context_mismatch",
        latency_ms=0,
    ).to_payload()


def _hours_window(hours: int) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return cutoff.strftime("ge%Y-%m-%dT%H:%M:%SZ")


def _coding_display(coding_list: list[dict[str, Any]] | None) -> str:
    if not coding_list:
        return ABSENT
    first = coding_list[0]
    return first.get("display") or first.get("code") or ABSENT


def _patient_demographics_fields(resource: dict[str, Any]) -> dict[str, Any]:
    name_obj = (resource.get("name") or [{}])[0]
    given = " ".join(name_obj.get("given") or []) or ABSENT
    family = name_obj.get("family") or ABSENT
    return {
        "given_name": given,
        "family_name": family,
        "birth_date": resource.get("birthDate") or ABSENT,
        "gender": resource.get("gender") or ABSENT,
    }


def _condition_fields(resource: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": _coding_display((resource.get("code") or {}).get("coding")),
        "clinical_status": _coding_display(
            (resource.get("clinicalStatus") or {}).get("coding")
        ),
        "verification_status": _coding_display(
            (resource.get("verificationStatus") or {}).get("coding")
        ),
        "recorded_date": resource.get("recordedDate") or ABSENT,
    }


def _medication_fields(resource: dict[str, Any]) -> dict[str, Any]:
    """MedicationRequest with §9 step 8 lifecycle canonicalization.

    Raw FHIR ``status='active'`` plus a ``dispenseRequest.validityPeriod.end``
    in the past means the order is *administratively* active but
    *clinically* expired. Surface that as ``lifecycle_status='expired'`` so
    the LLM doesn't have to interpret the multi-field semantics.
    """
    dosage = (resource.get("dosageInstruction") or [{}])[0]
    raw_status = resource.get("status") or "unknown"
    lifecycle = raw_status

    validity_end = (
        (resource.get("dispenseRequest") or {}).get("validityPeriod") or {}
    ).get("end")
    if raw_status == "active" and validity_end:
        try:
            from datetime import datetime as _dt

            end_dt = _dt.fromisoformat(validity_end.replace("Z", "+00:00"))
            now = _dt.now(end_dt.tzinfo)
            if end_dt < now:
                lifecycle = "expired"
        except (ValueError, AttributeError):
            # Malformed timestamp — fall through and let raw_status stand.
            pass

    return {
        "medication": _coding_display(
            (resource.get("medicationCodeableConcept") or {}).get("coding")
        ),
        "dosage": dosage.get("text") or "[not specified on order]",
        "lifecycle_status": lifecycle,
        "raw_status": raw_status,
        "validity_end": validity_end or ABSENT,
        "authored_on": resource.get("authoredOn") or ABSENT,
        "prescriber": (resource.get("requester") or {}).get("display") or ABSENT,
    }


def _observation_fields(resource: dict[str, Any]) -> dict[str, Any]:
    value = resource.get("valueString")
    if not value:
        vq = resource.get("valueQuantity") or {}
        if "value" in vq:
            unit = vq.get("unit") or ABSENT
            value = f"{vq['value']} {unit}"
    if not value:
        value = "[no value recorded]"
    notes = "; ".join(n.get("text", "") for n in (resource.get("note") or []))
    return {
        "code": _coding_display((resource.get("code") or {}).get("coding")),
        "value": value,
        "effective_time": resource.get("effectiveDateTime") or ABSENT,
        "note": notes or None,
    }


def _encounter_fields(resource: dict[str, Any]) -> dict[str, Any]:
    period = resource.get("period") or {}
    return {
        "class": (resource.get("class") or {}).get("code") or ABSENT,
        "start": period.get("start") or ABSENT,
        "end": period.get("end") or "[ongoing or not on file]",
        "reason": _coding_display(
            ((resource.get("reasonCode") or [{}])[0]).get("coding")
        ),
        "service_provider": (resource.get("serviceProvider") or {}).get("display") or ABSENT,
    }


def _service_request_fields(resource: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": _coding_display((resource.get("code") or {}).get("coding")),
        "status": resource.get("status") or ABSENT,
        "intent": resource.get("intent") or ABSENT,
        "authored_on": resource.get("authoredOn") or ABSENT,
        "requester": (resource.get("requester") or {}).get("display") or ABSENT,
        "reason": _coding_display(
            ((resource.get("reasonCode") or [{}])[0]).get("coding")
        ),
    }


def _diagnostic_report_fields(resource: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": _coding_display((resource.get("code") or {}).get("coding")),
        "category": _coding_display(
            ((resource.get("category") or [{}])[0]).get("coding")
        ),
        "effective_time": resource.get("effectiveDateTime") or ABSENT,
        "conclusion": resource.get("conclusion") or "[no impression on file]",
    }


def _medication_admin_fields(resource: dict[str, Any]) -> dict[str, Any]:
    """Apply §9 step 8 status canonicalization for medication administrations.

    Raw FHIR ``status`` can be ``in-progress``, ``completed``, ``not-done``,
    ``stopped``. We expose a ``lifecycle_status`` that's unambiguous, so the
    LLM doesn't have to interpret raw FHIR semantics.
    """
    raw_status = resource.get("status") or "unknown"
    lifecycle_map = {
        "completed": "given",
        "in-progress": "in-progress",
        "not-done": "held",
        "stopped": "stopped",
        "entered-in-error": "voided",
    }
    lifecycle = lifecycle_map.get(raw_status, raw_status)
    status_reason_codings = (resource.get("statusReason") or [{}])[0].get("coding")
    return {
        "medication": _coding_display(
            (resource.get("medicationCodeableConcept") or {}).get("coding")
        ),
        "lifecycle_status": lifecycle,
        "raw_status": raw_status,
        "effective_time": resource.get("effectiveDateTime") or ABSENT,
        "dose": (resource.get("dosage") or {}).get("text") or "[not specified on order]",
        "performer": ((resource.get("performer") or [{}])[0].get("actor") or {}).get(
            "display"
        )
        or ABSENT,
        "status_reason": _coding_display(status_reason_codings) if status_reason_codings else None,
    }


def _document_fields(resource: dict[str, Any]) -> dict[str, Any]:
    content = (resource.get("content") or [{}])[0].get("attachment") or {}
    body = content.get("data") or ""
    if body and len(body) > 100 and not any(c.isspace() for c in body[:80]):
        # base64 attachment — decode best-effort
        try:
            body = base64.b64decode(body).decode("utf-8", errors="replace")
        except Exception:
            body = "[attachment unavailable]"
    if len(body) > 4000:
        body = body[:4000] + " [truncated]"
    if not body:
        body = "[attachment unavailable]"
    return {
        "type": _coding_display((resource.get("type") or {}).get("coding")),
        "date": resource.get("date") or ABSENT,
        "author": ((resource.get("author") or [{}])[0]).get("display") or ABSENT,
        "body": body,
    }


def _wrap_patient_text(text: str | None, fhir_ref: str) -> str | None:
    """Sentinel-wrap patient-authored free text per ARCHITECTURE.md §9 step 7.

    The system prompt instructs the model to treat anything inside
    ``<patient-text>`` as data, never as instructions. Wrapping at the data
    layer means the model literally cannot see un-wrapped free text from the
    chart — every nursing note, observation comment, and document body
    arrives already labeled as untrusted.
    """
    if text is None or text == "" or (isinstance(text, str) and text.startswith("[")):
        # Don't wrap absence markers or empty values.
        return text
    return f'<patient-text id="{fhir_ref}">{text}</patient-text>'


def _result_from_entries(
    entries: list[dict[str, Any]],
    *,
    resource_type: str,
    field_extractor,
    sources: tuple[str, ...],
    error: str | None,
    latency_ms: int,
    ok: bool,
    sentinel_fields: tuple[str, ...] = (),
) -> ToolResult:
    rows: list[Row] = []
    for e in entries:
        fhir_ref = f"{resource_type}/{e.get('id', '?')}"
        fields = field_extractor(e)
        for field_name in sentinel_fields:
            if field_name in fields:
                fields[field_name] = _wrap_patient_text(fields[field_name], fhir_ref)
        rows.append(Row(fhir_ref=fhir_ref, resource_type=resource_type, fields=fields))
    return ToolResult(
        ok=ok,
        rows=tuple(rows),
        sources_checked=sources,
        error=error,
        latency_ms=latency_ms,
    )


def make_tools(settings: Settings) -> list[StructuredTool]:
    """Build the tool set bound to a shared FHIR client."""
    client = FhirClient(settings)

    async def get_patient_demographics(patient_id: str) -> dict[str, Any]:
        if (denied := _enforce_patient_context(patient_id)) is not None:
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
        if (denied := _enforce_patient_context(patient_id)) is not None:
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
        if (denied := _enforce_patient_context(patient_id)) is not None:
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
        if (denied := _enforce_patient_context(patient_id)) is not None:
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
        if (denied := _enforce_patient_context(patient_id)) is not None:
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
        if (denied := _enforce_patient_context(patient_id)) is not None:
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
        """UC-1 Stage 0: return the active user's care-team panel as patient
        ids. Fixture mode returns the canonical 5-patient panel; real mode
        will query OpenEMR's care-team relationships (TODO).

        Note: this tool is bound to the *user*, not a patient — so it does
        not enforce the patient-context guard. The downstream tools that
        actually fetch patient data still do.
        """
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

    async def get_change_signal(patient_id: str, hours: int = 24) -> dict[str, Any]:
        """UC-1 Stage 1: lightweight change-signal probe.

        Returns a row per signal channel (vital-signs, labs, document refs,
        encounters) carrying just the count over the lookback window. This
        is a §10 fan-out primitive — fast, no body fetches, intended to be
        called in parallel across every patient on the panel.

        Honors the patient-context guard: when an active SMART patient is
        bound (UC-2 / per-patient mode), this tool refuses to probe a
        different patient. Triage_node clears the active context so probes
        across the panel succeed.
        """
        if (denied := _enforce_patient_context(patient_id)) is not None:
            return denied
        params = {"patient": patient_id, "date": _hours_window(hours)}
        # Four signal channels (§10).
        signals: list[tuple[str, dict[str, Any]]] = [
            ("vital-signs", {**params, "category": "vital-signs"}),
            ("labs", {**params, "category": "laboratory"}),
            ("encounters", params.copy()),
            ("documents", params.copy()),
        ]
        rows: list[Row] = []
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
            rows.append(
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
            rows=tuple(rows),
            sources_checked=tuple(s for s, _ in signals),
            error=last_err,
            latency_ms=latency_total,
        ).to_payload()

    async def get_recent_orders(patient_id: str, hours: int = 24) -> dict[str, Any]:
        if (denied := _enforce_patient_context(patient_id)) is not None:
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
        if (denied := _enforce_patient_context(patient_id)) is not None:
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
        if (denied := _enforce_patient_context(patient_id)) is not None:
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
        if (denied := _enforce_patient_context(patient_id)) is not None:
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

    return [
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
