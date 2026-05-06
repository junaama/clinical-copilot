"""Persistence for document extractions.

Two write paths:

* ``DocumentExtractionStore`` — INSERT lab extractions into the agent's
  Postgres (``document_extractions`` table). Lab values have no FHIR write
  path on OpenEMR; W2 PRD calls this the document-annotation model.
  Citations point at ``DocumentReference/{id}`` plus a field path.

* ``IntakePersister`` — write intake-form-derived facts to OpenEMR via
  the Standard API (``allergies``, ``medications``, ``medical_problems``)
  and update Patient demographics via FHIR (the one FHIR write path that
  works on OpenEMR).

Both classes accept their dependencies in the constructor — no global
state, no module-level singletons — so they can be swapped for fakes in
tests.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .schemas import FieldWithBBox

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..fhir import FhirClient
    from ..standard_api_client import StandardApiClient
    from .schemas import IntakeExtraction, LabExtraction

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class IntakeWriteSummary:
    """Per-section result of writing an intake extraction to OpenEMR."""

    allergy_ids: tuple[str, ...]
    medication_ids: tuple[str, ...]
    medical_problem_ids: tuple[str, ...]
    demographics_updated: bool
    errors: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "allergy_ids": list(self.allergy_ids),
            "medication_ids": list(self.medication_ids),
            "medical_problem_ids": list(self.medical_problem_ids),
            "demographics_updated": self.demographics_updated,
            "errors": list(self.errors),
        }


class DocumentExtractionStore:
    """Async store for lab extractions in the agent's Postgres.

    The ``document_extractions`` table holds one row per (document_id,
    extraction-attempt) — the agent does not de-duplicate on document_id
    so that re-extraction with a newer model produces a new row that can
    be diffed against the prior one.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    async def save_lab_extraction(
        self,
        *,
        extraction: LabExtraction,
        bboxes: list[FieldWithBBox],
        document_id: str,
        patient_id: str,
    ) -> int:
        """Insert one extraction row and return its primary-key id."""
        return await self._insert(
            doc_type="lab_pdf",
            extraction_json=extraction.model_dump(mode="json"),
            bboxes_json=[b.model_dump(mode="json") for b in bboxes],
            document_id=document_id,
            patient_id=patient_id,
        )

    async def save_intake_extraction(
        self,
        *,
        extraction: IntakeExtraction,
        bboxes: list[FieldWithBBox],
        document_id: str,
        patient_id: str,
    ) -> int:
        """Insert an intake extraction (mirror of save_lab for trace fidelity).

        Intake-derived facts are written to OpenEMR — but keeping the
        original extraction here lets us audit-after-the-fact whether
        a particular OpenEMR write came from a particular document.
        """
        return await self._insert(
            doc_type="intake_form",
            extraction_json=extraction.model_dump(mode="json"),
            bboxes_json=[b.model_dump(mode="json") for b in bboxes],
            document_id=document_id,
            patient_id=patient_id,
        )

    async def _insert(
        self,
        *,
        doc_type: str,
        extraction_json: dict[str, Any],
        bboxes_json: list[dict[str, Any]],
        document_id: str,
        patient_id: str,
    ) -> int:
        try:
            from psycopg_pool import AsyncConnectionPool
        except ImportError as exc:  # pragma: no cover - install-time guard
            raise RuntimeError(
                "DocumentExtractionStore requires the 'postgres' extra. "
                "Install with: uv sync --extra postgres"
            ) from exc

        pool = AsyncConnectionPool(self._dsn, open=False, min_size=1, max_size=2)
        await pool.open()
        try:
            async with pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        INSERT INTO document_extractions
                            (document_id, patient_id, doc_type,
                             extraction_json, bboxes_json)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        (
                            document_id,
                            patient_id,
                            doc_type,
                            json.dumps(extraction_json),
                            json.dumps(bboxes_json),
                        ),
                    )
                    row = await cur.fetchone()
                    if not row:
                        raise RuntimeError("INSERT did not return id")
                    return int(row[0])
        finally:
            await pool.close()


class IntakePersister:
    """Writes intake-form-derived facts to OpenEMR.

    Allergies, medications, and medical problems are POSTed via the
    Standard API. Demographics are PUT to the FHIR Patient endpoint.
    Each section is best-effort: a single failure is captured in
    ``errors`` and does not abort the remaining writes.
    """

    def __init__(
        self,
        *,
        std_client: StandardApiClient,
        fhir_client: FhirClient,
    ) -> None:
        self._std = std_client
        self._fhir = fhir_client

    async def persist_intake(
        self,
        *,
        patient_id: str,
        extraction: IntakeExtraction,
    ) -> IntakeWriteSummary:
        """Write every intake section. Returns a summary of ids + errors."""
        allergy_ids: list[str] = []
        medication_ids: list[str] = []
        problem_ids: list[str] = []
        errors: list[str] = []

        for allergy in extraction.allergies:
            payload = _allergy_payload(allergy)
            ok, aid, err, _ms = await self._std.create_allergy(patient_id, payload)
            if ok and aid:
                allergy_ids.append(aid)
            else:
                errors.append(f"allergy '{allergy.substance}': {err or 'unknown'}")

        for medication in extraction.current_medications:
            payload = _medication_payload(medication)
            ok, mid, err, _ms = await self._std.create_medication(patient_id, payload)
            if ok and mid:
                medication_ids.append(mid)
            else:
                errors.append(f"medication '{medication.name}': {err or 'unknown'}")

        # Chief concern is captured as a medical problem so it shows up in
        # OpenEMR's problem-list view alongside structured diagnoses.
        chief_concern = (extraction.chief_concern or "").strip()
        if chief_concern:
            ok, pid, err, _ms = await self._std.create_medical_problem(
                patient_id,
                {"title": chief_concern},
            )
            if ok and pid:
                problem_ids.append(pid)
            else:
                errors.append(f"chief_concern: {err or 'unknown'}")

        attempted, demographics_updated, demo_error = (
            await self._update_demographics(patient_id, extraction)
        )
        if attempted and not demographics_updated:
            errors.append(f"demographics: {demo_error or 'update failed'}")

        return IntakeWriteSummary(
            allergy_ids=tuple(allergy_ids),
            medication_ids=tuple(medication_ids),
            medical_problem_ids=tuple(problem_ids),
            demographics_updated=demographics_updated,
            errors=tuple(errors),
        )

    async def _update_demographics(
        self, patient_id: str, extraction: IntakeExtraction
    ) -> tuple[bool, bool, str | None]:
        """Run the demographics PUT.

        Returns ``(attempted, succeeded, error)``. ``attempted=False``
        means the demographics block was empty (every field null) — no
        PUT happened and there is no error worth surfacing. When
        ``attempted=True`` the call ran; ``succeeded`` reflects whether
        FHIR returned ok.
        """
        demographics = extraction.demographics
        if demographics is None:
            return False, False, None
        body = _patient_resource_from_demographics(patient_id, demographics)
        if body is None:
            return False, False, None
        update = getattr(self._fhir, "update_patient", None)
        if update is None:
            _log.warning("FhirClient has no update_patient; skipping demographics")
            return True, False, "update_patient unsupported"
        ok, _resource, err, _ms = await update(patient_id, body)
        if not ok:
            _log.warning("update_patient failed: %s", err)
        return True, bool(ok), err


# ---------------------------------------------------------------------------
# Pure helpers — payload shapes for the Standard API + FHIR write
# ---------------------------------------------------------------------------


def _allergy_payload(allergy: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"title": allergy.substance}
    if getattr(allergy, "severity", None):
        payload["severity_al"] = allergy.severity
    if getattr(allergy, "reaction", None):
        payload["reaction"] = allergy.reaction
    return payload


def _medication_payload(medication: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"title": medication.name}
    dose = getattr(medication, "dose", None)
    frequency = getattr(medication, "frequency", None)
    if dose and frequency:
        payload["dosage"] = f"{dose} {frequency}"
    elif dose:
        payload["dosage"] = dose
    elif frequency:
        payload["dosage"] = frequency
    return payload


def _patient_resource_from_demographics(
    patient_id: str, demographics: Any
) -> dict[str, Any] | None:
    """Build a FHIR Patient resource from intake demographics.

    Returns ``None`` when the demographics block is empty (every field
    null) — there is no reason to PUT a no-op update.
    """
    name = getattr(demographics, "name", None)
    dob = getattr(demographics, "dob", None)
    gender = getattr(demographics, "gender", None)
    address = getattr(demographics, "address", None)
    phone = getattr(demographics, "phone", None)

    has_any = any(field for field in (name, dob, gender, address, phone))
    if not has_any:
        return None

    body: dict[str, Any] = {
        "resourceType": "Patient",
        "id": patient_id,
    }
    if name:
        family, given = _split_name(name)
        body["name"] = [
            {"use": "official", "family": family, "given": given},
        ]
    if dob:
        body["birthDate"] = dob
    if gender:
        body["gender"] = gender
    if phone:
        body["telecom"] = [{"system": "phone", "value": phone, "use": "home"}]
    if address:
        body["address"] = [{"text": address}]
    return body


def _split_name(name: str) -> tuple[str, list[str]]:
    """Split ``"First Middle Last"`` into ``("Last", ["First", "Middle"])``.

    Single-word names map to ``("", [name])`` — FHIR requires at least one
    of family/given to be present and a single token is more naturally a
    given name than a family name.
    """
    parts = [p for p in name.split() if p]
    if not parts:
        return "", []
    if len(parts) == 1:
        return "", [parts[0]]
    return parts[-1], parts[:-1]
