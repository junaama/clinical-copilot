"""Shared utilities for the tools package.

Field extractors, authorization enforcement, result builders, and patient
name-matching helpers. All pure or near-pure functions — no tool
registrations, no LangChain dependencies.
"""

from __future__ import annotations

import base64
import contextvars
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ..care_team import AuthDecision, CareTeamGate
from ..fhir import ABSENT, Row, ToolResult

# ---------------------------------------------------------------------------
# Context variables
# ---------------------------------------------------------------------------

_active_smart_token: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "copilot_active_smart_token", default=None
)
_active_user_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "copilot_active_user_id", default=None
)
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


# ---------------------------------------------------------------------------
# Authorization helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def _hours_window(hours: int) -> str:
    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    return cutoff.strftime("ge%Y-%m-%dT%H:%M:%SZ")


def _hours_until_now_from_iso(since: str) -> int | None:
    """Convert an ISO 8601 ``since`` timestamp to a positive hours-ago delta.

    Returns ``None`` for malformed input or for timestamps in the future —
    both are caller errors that ``run_recent_changes`` surfaces as
    ``invalid_since``. The +1 buffer rounds the cutoff *outward* so the
    boundary timestamp is included rather than excluded by integer
    truncation; the W-9 diff loses very little by overshooting the
    window by an hour and would lose a real signal by undershooting it.
    """
    if not since:
        return None
    try:
        parsed = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    delta = datetime.now(UTC) - parsed
    seconds = delta.total_seconds()
    if seconds < 0:
        return None
    return int(seconds // 3600) + 1


# ---------------------------------------------------------------------------
# FHIR field extractors
# ---------------------------------------------------------------------------


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
    """MedicationRequest with lifecycle canonicalization.

    Raw FHIR ``status='active'`` plus a ``dispenseRequest.validityPeriod.end``
    in the past means the order is *administratively* active but
    *clinically* expired. Surface that as ``lifecycle_status='expired'`` so
    the LLM doesn't have to interpret the multi-field semantics.
    """
    _raw_dosage = (resource.get("dosageInstruction") or [{}])[0]
    dosage = _raw_dosage if isinstance(_raw_dosage, dict) else {}
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
    """Apply status canonicalization for medication administrations.

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


# ---------------------------------------------------------------------------
# Sentinel wrapping
# ---------------------------------------------------------------------------


def _wrap_patient_text(text: str | None, fhir_ref: str) -> str | None:
    """Sentinel-wrap patient-authored free text.

    The system prompt instructs the model to treat anything inside
    ``<patient-text>`` as data, never as instructions.
    """
    if text is None or text == "" or (isinstance(text, str) and text.startswith("[")):
        return text
    return f'<patient-text id="{fhir_ref}">{text}</patient-text>'


# ---------------------------------------------------------------------------
# Result builder
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Patient name matching
# ---------------------------------------------------------------------------

_SYNTHETIC_NAME_TOKEN_RE = re.compile(r"\b([A-Za-zÀ-ÖØ-öø-ÿ'\u2019-]+)\d+\b")


def _normalize_patient_name_for_match(value: str) -> str:
    """Strip synthetic Synthea/OpenEMR suffixes before name matching."""
    cleaned = _SYNTHETIC_NAME_TOKEN_RE.sub(r"\1", value)
    return " ".join(cleaned.lower().split())


def _patient_matches_name(
    patient: dict[str, Any], name_lower: str
) -> bool:
    """Case-insensitive name match: family, given, full-name, or substring."""
    query = _normalize_patient_name_for_match(name_lower)
    given = _normalize_patient_name_for_match(patient.get("given_name") or "")
    family = _normalize_patient_name_for_match(patient.get("family_name") or "")
    full_a = f"{given} {family}".strip()
    full_b = f"{family} {given}".strip()
    if not query:
        return False
    return (
        query == family
        or query == given
        or query in full_a
        or query in full_b
        or query in family
        or query in given
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


# ---------------------------------------------------------------------------
# Merge helpers (used by composite tools)
# ---------------------------------------------------------------------------


def _merge_envelopes(
    results: list[dict[str, Any]],
    *,
    initial_sources: tuple[str, ...] = (),
    elapsed_ms: int,
) -> dict[str, Any]:
    """Merge fan-out tool-result payloads into one granular envelope."""
    merged_rows: list[dict[str, Any]] = []
    merged_sources: list[str] = list(initial_sources)
    first_error: str | None = None
    any_failed = False
    auth_denial_values = {
        d.value for d in AuthDecision if d is not AuthDecision.ALLOWED
    }
    for payload in results:
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


def _merge_panel_envelopes(
    nested: list[tuple[dict[str, Any], ...]],
    *,
    panel_source: str,
    elapsed_ms: int,
) -> dict[str, Any]:
    """Flatten per-pid sub-fan-out results and merge via ``_merge_envelopes``."""
    flat: list[dict[str, Any]] = [
        payload for per_pid in nested for payload in per_pid
    ]
    return _merge_envelopes(
        flat,
        initial_sources=(panel_source,),
        elapsed_ms=elapsed_ms,
    )
