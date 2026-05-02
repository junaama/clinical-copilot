"""LangChain-tool-decorated functions the agent can call.

Each tool is a thin wrapper over ``FhirClient`` plus field allowlist + absence
markers (ARCHITECTURE §11, §15). All async; all return JSON-serializable dicts
so they can be embedded in tool messages.

Convention: every patient-data tool runs through ``CareTeamGate`` before
issuing a FHIR query. The gate checks whether the active practitioner (the
``user_id`` contextvar set by the graph) is on the patient's CareTeam; if
not, the call is hard-refused with ``error='careteam_denied'`` (or
``no_active_patient`` when the tool was called without a patient_id at all).
The legacy EHR-launch-era one-patient-per-conversation pin is gone — issue 003
removed it; multi-patient conversations resolve patients via the
``resolve_patient`` tool against the user's CareTeam.

Tests and scripts that exercise patient-scoped tools must bind an active
user via ``set_active_user_id`` (typically a fixture practitioner) so the
gate can decide.
"""

from __future__ import annotations

import asyncio
import base64
import contextvars
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.tools import StructuredTool

from .care_team import AuthDecision, CareTeamGate
from .config import Settings
from .fhir import ABSENT, FhirClient, Row, ToolResult
from .fixtures import CARE_TEAM_PANEL

# Contextvars carry the active SMART context into the async tool calls
# without threading it through every signature. Set by the graph's agent_node
# before invoking the agent; auto-propagates to spawned asyncio tasks.
_active_smart_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "copilot_active_smart_token", default=None
)
_active_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "copilot_active_user_id", default=None
)
# Conversation-scoped patient registry. Set by the graph's agent_node from
# ``state.resolved_patients`` before the agent runs; ``resolve_patient`` reads
# it for O(1) cache hits on previously-resolved names. Read-only at the tool
# layer — the agent_node aggregates new resolutions back into state after
# all tools have returned. Default is ``None`` (sentinel for "not bound");
# ``get_active_registry`` normalizes to an empty dict so callers don't have
# to.
_active_registry: contextvars.ContextVar[dict[str, dict[str, Any]] | None] = (
    contextvars.ContextVar("copilot_active_registry", default=None)
)


def set_active_smart_token(token: str | None) -> None:
    _active_smart_token.set(token or None)


def get_active_smart_token() -> str | None:
    return _active_smart_token.get()


def set_active_user_id(user_id: str | None) -> None:
    _active_user_id.set(user_id or None)


def get_active_user_id() -> str | None:
    return _active_user_id.get()


def set_active_registry(registry: dict[str, dict[str, Any]] | None) -> None:
    _active_registry.set(dict(registry or {}))


def get_active_registry() -> dict[str, dict[str, Any]]:
    return _active_registry.get() or {}


def _denial_payload(decision: AuthDecision) -> dict[str, Any]:
    return ToolResult(
        ok=False,
        sources_checked=(),
        error=decision.value,
        latency_ms=0,
    ).to_payload()


async def _enforce_patient_authorization(
    gate: CareTeamGate, patient_id: str
) -> dict[str, Any] | None:
    """Run the CareTeam gate for this tool call.

    Returns the refusal payload when the gate denies, or ``None`` when the
    call is allowed. Tests must bind an active user_id (fixture
    practitioner) before invoking patient-scoped tools — without one the
    gate returns ``careteam_denied`` for every patient.
    """
    user_id = get_active_user_id() or ""
    decision = await gate.assert_authorized(user_id, patient_id)
    if decision is AuthDecision.ALLOWED:
        return None
    return _denial_payload(decision)


def _hours_window(hours: int) -> str:
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
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


def _patient_matches_name(
    patient: dict[str, Any], name_lower: str
) -> bool:
    """Case-insensitive name match: family, given, full-name, or substring.

    Accepts "Hayes", "Robert Hayes", "robert", or even "haye" (substring of
    family). Two-word inputs are matched as either order — "Robert Hayes" and
    "Hayes, Robert" both hit the same row.
    """
    given = (patient.get("given_name") or "").lower()
    family = (patient.get("family_name") or "").lower()
    full_a = f"{given} {family}".strip()
    full_b = f"{family} {given}".strip()
    if not name_lower:
        return False
    return (
        name_lower == family
        or name_lower == given
        or name_lower in full_a
        or name_lower in full_b
        or name_lower in family
        or name_lower in given
    )


def _registry_to_patient_dict(
    p: dict[str, Any],
) -> dict[str, Any]:
    """Project a registry entry into the resolve_patient response shape."""
    return {
        "patient_id": p["patient_id"],
        "given_name": p["given_name"],
        "family_name": p["family_name"],
        "birth_date": p["birth_date"],
    }


def make_tools(settings: Settings) -> list[StructuredTool]:
    """Build the tool set bound to a shared FHIR client and CareTeam gate."""
    client = FhirClient(settings)
    gate = CareTeamGate(client, admin_user_ids=frozenset(settings.admin_user_ids))

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
        on a single-name match before paying a FHIR roundtrip. The audit
        row still reflects the call regardless — every "user mentioned a
        patient" event is logged via the gate-decisions array.
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

        # Cache-first: scan the registry for a unique match. DOB and
        # mrn_tail filters apply to the cache too so a more-specific call
        # narrows previously-resolved entries.
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

        # Cold path: ask the gate for the user's panel and match against it.
        # ``list_panel`` is the same call the empty-state UI uses; we reuse
        # it so the resolver is intrinsically CareTeam-prefiltered.
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
            # ``mrn_tail`` is accepted for forward-compat but the fixture
            # bundle has no MRN identifiers; real OpenEMR will populate
            # ``Patient.identifier`` and we'll filter on the trailing digits
            # here. Until then, mrn_tail-narrowing is a no-op when MRN data
            # is unavailable.
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
        """UC-1 Stage 0: return the active user's care-team panel as patient
        ids.

        Two modes:

        - Fixture mode (``USE_FIXTURE_FHIR=1``) — returns the canonical
          5-patient panel from ``fixtures.CARE_TEAM_PANEL``. Used for tests
          and the demo runner.
        - Real mode — queries the FHIR ``CareTeam`` resource scoped to the
          authenticated practitioner (``CareTeam?participant=Practitioner/{id}``)
          and extracts unique ``Patient/{id}`` references from each team's
          ``subject``. When the SMART token doesn't surface a practitioner
          id (e.g. patient-launch context), returns an explicit error rather
          than falling back to a fixture panel.

        Note: this tool is bound to the *user*, not a patient — so it does
        not enforce the patient-context guard. The downstream tools that
        actually fetch patient data still do.
        """
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

        # CareTeam.subject -> Patient/{id}. Dedupe while preserving order.
        seen: set[str] = set()
        rows: list[Row] = []
        for team in entries:
            ref = (team.get("subject") or {}).get("reference") or ""
            if not ref.startswith("Patient/"):
                continue
            pid = ref.removeprefix("Patient/")
            if pid in seen:
                continue
            seen.add(pid)
            rows.append(
                Row(
                    fhir_ref=ref,
                    resource_type="Patient",
                    fields={"patient_id": pid},
                )
            )
        return ToolResult(
            ok=True,
            rows=tuple(rows),
            sources_checked=("CareTeam",),
            latency_ms=ms,
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
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
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

    def _merge_panel_envelopes(
        nested: list[tuple[dict[str, Any], ...]],
        *,
        panel_source: str,
        elapsed_ms: int,
    ) -> dict[str, Any]:
        """Merge per-pid sub-fan-out results into one granular envelope.

        Mirrors the granular ``ToolResult.to_payload()`` shape so the
        verifier loop and citation-card mapper see the composite as just
        another tool. Auth-class denials in any nested call bubble up as
        the composite's response (so the LLM gets the same denial it
        would see for a granular call); non-auth errors record the first
        failure but let other branches' rows through, matching
        granular-tool failure semantics.
        """
        merged_rows: list[dict[str, Any]] = []
        merged_sources: list[str] = [panel_source]
        first_error: str | None = None
        any_failed = False
        auth_denial_values = {
            d.value for d in AuthDecision if d is not AuthDecision.ALLOWED
        }
        for per_pid_results in nested:
            for payload in per_pid_results:
                if (
                    not payload.get("ok")
                    and payload.get("error") in auth_denial_values
                ):
                    return payload
                merged_rows.extend(payload.get("rows") or [])
                for source in payload.get("sources_checked") or []:
                    if source not in merged_sources:
                        merged_sources.append(source)
                if not payload.get("ok"):
                    any_failed = True
                    if first_error is None:
                        first_error = payload.get("error")
        return {
            "ok": not any_failed,
            "rows": merged_rows,
            "sources_checked": merged_sources,
            "error": first_error,
            "latency_ms": elapsed_ms,
        }

    async def run_panel_triage(hours: int = 24) -> dict[str, Any]:
        """Composite tool: rank the user's CareTeam panel by overnight signal.

        Implementation per issue 007: ``gate.list_panel(user_id)`` →
        per-pid parallel fan-out of ``get_change_signal`` (24h count
        probe), ``get_patient_demographics``, and ``get_active_problems``.
        All per-pid sub-fans-out run concurrently under one outer
        ``asyncio.gather``, so wall-clock time is roughly one slow call
        regardless of panel size.

        Authorization is intrinsically CareTeam-bounded by virtue of
        ``list_panel`` returning only the user's own roster — no
        out-of-team patient can leak through. Each per-pid nested call
        re-runs the gate as defense in depth: a buggy refactor that
        widened ``list_panel`` would still be caught at the call site.

        The merge envelope is byte-for-byte the granular tool shape
        (``rows``, ``sources_checked``, ``latency_ms``, ``error``,
        ``ok``) so the verifier loop and citation cards downstream
        don't have to special-case the composite.
        """
        user_id = get_active_user_id() or ""
        roster = await gate.list_panel(user_id)

        if not roster:
            # Empty panel is not an error — the user just has no patients
            # assigned yet. Return an empty-but-ok envelope so the LLM
            # can say so without a refusal narrative.
            return ToolResult(
                ok=True,
                rows=(),
                sources_checked=("CareTeam (panel)",),
                latency_ms=0,
            ).to_payload()

        started = time.monotonic()

        async def _per_pid(pid: str) -> tuple[dict[str, Any], ...]:
            return await asyncio.gather(
                get_change_signal(pid, hours),
                get_patient_demographics(pid),
                get_active_problems(pid),
            )

        nested = await asyncio.gather(*[_per_pid(p.patient_id) for p in roster])
        elapsed_ms = int((time.monotonic() - started) * 1000)

        return _merge_panel_envelopes(
            list(nested), panel_source="CareTeam (panel)", elapsed_ms=elapsed_ms
        )

    async def run_panel_med_safety(hours: int = 24) -> dict[str, Any]:
        """Composite tool: pharmacist-style med-safety scan across the panel.

        Implementation per issue 007: ``gate.list_panel(user_id)`` →
        per-pid parallel fan-out of ``get_active_medications`` and
        ``get_recent_labs`` (default 24h). All per-pid sub-fans-out run
        concurrently under one outer ``asyncio.gather``, so wall-clock
        time is roughly one slow call regardless of panel size.

        The fan-out returns *all* recent labs, not just renal/hepatic
        markers. The W-10 synthesis framing tells the LLM which lab
        codes to lens through (creatinine, K+, AST/ALT, INR, anti-Xa,
        etc.); pre-filtering at the data layer would couple the
        composite to a specific lab vocabulary and break as we add
        new med-safety patterns. Letting the model apply the lens means
        new med-safety questions add a prompt change, not a tool change.

        Authorization is intrinsically CareTeam-bounded by virtue of
        ``list_panel`` returning only the user's own roster. Each
        per-pid nested call re-runs the gate as defense in depth — a
        buggy refactor that widened ``list_panel`` would still be
        caught at the call site.

        The merge envelope is byte-for-byte the granular tool shape
        (``rows``, ``sources_checked``, ``latency_ms``, ``error``,
        ``ok``) so the verifier loop and citation cards downstream
        don't have to special-case the composite.
        """
        user_id = get_active_user_id() or ""
        roster = await gate.list_panel(user_id)

        if not roster:
            # Empty panel is not an error — the user just has no patients
            # assigned yet. Return an empty-but-ok envelope so the LLM
            # can say so without a refusal narrative.
            return ToolResult(
                ok=True,
                rows=(),
                sources_checked=("CareTeam (panel)",),
                latency_ms=0,
            ).to_payload()

        started = time.monotonic()

        async def _per_pid(pid: str) -> tuple[dict[str, Any], ...]:
            return await asyncio.gather(
                get_active_medications(pid),
                get_recent_labs(pid, hours),
            )

        nested = await asyncio.gather(*[_per_pid(p.patient_id) for p in roster])
        elapsed_ms = int((time.monotonic() - started) * 1000)

        return _merge_panel_envelopes(
            list(nested), panel_source="CareTeam (panel)", elapsed_ms=elapsed_ms
        )

    async def run_per_patient_brief(
        patient_id: str, hours: int = 24
    ) -> dict[str, Any]:
        """Composite tool: fan out the six per-patient brief reads in parallel.

        Wraps demographics, active problems, active meds, recent vitals,
        recent labs, and recent encounters in one ``asyncio.gather`` and
        merges their rows into one envelope shaped exactly like a granular
        tool's ``ToolResult.to_payload()``. The verifier loop and citation
        machinery downstream don't have to special-case the composite.

        Authorization is enforced **per nested call** by reusing the
        granular tools' ``_enforce_patient_authorization`` helper. A buggy
        refactor that removed the gate from a single branch would still
        be caught by the gate at the other branches; defense in depth.
        """
        # Top-of-call gate: short-circuits the empty-pid / out-of-team path
        # so we don't fan out six gate-denied calls when one would do.
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied

        started = time.monotonic()
        results = await asyncio.gather(
            get_patient_demographics(patient_id),
            get_active_problems(patient_id),
            get_active_medications(patient_id),
            get_recent_vitals(patient_id, hours),
            get_recent_labs(patient_id, hours),
            get_recent_encounters(patient_id, hours),
        )
        elapsed_ms = int((time.monotonic() - started) * 1000)

        merged_rows: list[dict[str, Any]] = []
        merged_sources: list[str] = []
        first_error: str | None = None
        any_failed = False
        for payload in results:
            # If any nested call hit the gate's deny path, bubble that
            # decision up — the LLM should see the same authorization
            # error it would see for a granular call.
            if not payload.get("ok") and payload.get("error") in {
                d.value for d in AuthDecision if d is not AuthDecision.ALLOWED
            }:
                return payload
            merged_rows.extend(payload.get("rows") or [])
            for source in payload.get("sources_checked") or []:
                if source not in merged_sources:
                    merged_sources.append(source)
            if not payload.get("ok"):
                any_failed = True
                if first_error is None:
                    first_error = payload.get("error")

        return {
            "ok": not any_failed,
            "rows": merged_rows,
            "sources_checked": merged_sources,
            "error": first_error,
            "latency_ms": elapsed_ms,
        }

    return [
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
        StructuredTool.from_function(
            coroutine=run_per_patient_brief,
            name="run_per_patient_brief",
            description=(
                "Composite per-patient brief: fans out demographics, active "
                "problems, active medications, recent vitals (24h), recent "
                "labs (24h), and recent encounters (24h) in PARALLEL and "
                "returns one merged envelope. Prefer this tool over the "
                "granular reads whenever the user asks for an overview, a "
                "brief, a quick picture, or 'what happened to <patient>' — "
                "it is materially faster than chaining the six granular "
                "calls. Returns the same envelope shape (rows, "
                "sources_checked, latency_ms, error, ok) so citation "
                "verification is unchanged. For a single targeted question "
                "(one specific lab, one specific vital), use the granular "
                "tool instead."
            ),
        ),
        StructuredTool.from_function(
            coroutine=run_panel_triage,
            name="run_panel_triage",
            description=(
                "Composite panel triage: rank the user's CareTeam panel by "
                "overnight signal so the clinician knows who to see first. "
                "Implementation: list_panel → per-pid parallel fan-out of "
                "change-signal counts (vitals, labs, encounters, document "
                "refs in the lookback window, default 24h), demographics, "
                "and active problems. Use this tool whenever the user asks "
                "about prioritization across the panel — 'who needs "
                "attention?', 'who do I need to see first?', 'anyone "
                "deteriorating?', 'morning rounds order'. Returns the same "
                "envelope shape (rows, sources_checked, latency_ms, error, "
                "ok) as a granular tool. The roster is intrinsically "
                "CareTeam-bounded; no patient outside the user's panel can "
                "appear. No arguments other than an optional ``hours`` "
                "lookback window."
            ),
        ),
        StructuredTool.from_function(
            coroutine=run_panel_med_safety,
            name="run_panel_med_safety",
            description=(
                "Composite panel med-safety scan: pharmacist-style review "
                "across the user's CareTeam panel. Implementation: "
                "list_panel → per-pid parallel fan-out of active "
                "medications and recent labs (default 24h). Use this tool "
                "whenever the user asks about medication-safety review "
                "across the panel — 'which patients need a med review?', "
                "'any pharmacy concerns this morning?', 'who's on a renally "
                "cleared med with rising creatinine?', 'anyone hyperkalemic "
                "on an ACE?'. The returned envelope contains all active "
                "meds plus all recent labs per patient; the synthesis "
                "framing applies the renal / hepatic / anticoagulant lens "
                "and surfaces the safety-relevant combinations. Returns "
                "the same envelope shape (rows, sources_checked, "
                "latency_ms, error, ok) as a granular tool. The roster is "
                "intrinsically CareTeam-bounded; no patient outside the "
                "user's panel can appear. No arguments other than an "
                "optional ``hours`` lookback window. For a single-patient "
                "med-safety question (e.g., 'should THIS patient still be "
                "on broad-spectrum?'), use the granular tools instead."
            ),
        ),
    ]
