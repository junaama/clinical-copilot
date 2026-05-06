"""Unit tests for ``copilot.extraction.persistence`` (issue 006).

Covers ``IntakePersister`` end-to-end against fakes for
``StandardApiClient`` / ``FhirClient`` and ``DocumentExtractionStore``
through a mocked ``AsyncConnectionPool`` so we don't need a real
Postgres for the unit suite.

What's tested (external behavior only):

* ``IntakePersister.persist_intake`` — every intake section that has
  data is dispatched to the right Standard-API endpoint; ids accumulate
  in the result; failures land in ``errors`` without aborting the
  remaining writes.
* Empty intake sections are no-ops (no API call, no error).
* Demographics PUT is skipped when the demographics block is entirely
  null and attempted otherwise; failure is captured as a non-fatal
  error.
* ``DocumentExtractionStore.save_lab_extraction`` /
  ``save_intake_extraction`` execute one ``INSERT … RETURNING id`` and
  return the id as an int.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from copilot.extraction.persistence import (
    DocumentExtractionStore,
    IntakePersister,
    _patient_resource_from_demographics,
    _split_name,
)
from copilot.extraction.schemas import (
    IntakeAllergy,
    IntakeDemographics,
    IntakeExtraction,
    IntakeMedication,
    LabExtraction,
    LabResult,
    SourceCitation,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _intake_payload(
    *,
    chief_concern: str = "shortness of breath",
    medications: list[dict[str, Any]] | None = None,
    allergies: list[dict[str, Any]] | None = None,
    demographics: dict[str, Any] | None = None,
) -> IntakeExtraction:
    citation = SourceCitation(
        source_type="intake_form",
        source_id="DocumentReference/doc-1",
    )
    return IntakeExtraction(
        demographics=IntakeDemographics(**(demographics or {})),
        chief_concern=chief_concern,
        current_medications=[IntakeMedication(**m) for m in (medications or [])],
        allergies=[IntakeAllergy(**a) for a in (allergies or [])],
        family_history=[],
        social_history=None,
        source_citation=citation,
        source_document_id="DocumentReference/doc-1",
        extraction_model="claude-sonnet-4",
        extraction_timestamp="2026-05-06T12:00:00Z",
    )


def _lab_payload() -> LabExtraction:
    citation = SourceCitation(
        source_type="lab_pdf",
        source_id="DocumentReference/lab-1",
    )
    return LabExtraction(
        results=[
            LabResult(
                test_name="LDL",
                value="180",
                unit="mg/dL",
                reference_range="<100",
                collection_date="2026-04-15",
                abnormal_flag="high",
                confidence="high",
                source_citation=citation,
            )
        ],
        source_document_id="DocumentReference/lab-1",
        extraction_model="claude-sonnet-4",
        extraction_timestamp="2026-05-06T12:00:00Z",
    )


def _cached_lab_row(
    *,
    patient_id: str = "patient-1",
    document_id: str = "doc-1",
    filename: str = "lab.pdf",
    content_sha256: str = "sha-abc",
) -> dict[str, Any]:
    return {
        "id": 42,
        "document_id": document_id,
        "patient_id": patient_id,
        "doc_type": "lab_pdf",
        "extraction_json": _lab_payload().model_dump(mode="json"),
        "bboxes_json": [],
        "filename": filename,
        "content_sha256": content_sha256,
    }


# ---------------------------------------------------------------------------
# IntakePersister
# ---------------------------------------------------------------------------


def _ok(_id: str) -> tuple[bool, str, None, int]:
    return True, _id, None, 5


def _err(reason: str) -> tuple[bool, None, str, int]:
    return False, None, reason, 5


@pytest.fixture()
def std_client() -> MagicMock:
    client = MagicMock()
    client.create_allergy = AsyncMock(return_value=_ok("a-1"))
    client.create_medication = AsyncMock(return_value=_ok("m-1"))
    client.create_medical_problem = AsyncMock(return_value=_ok("p-1"))
    return client


@pytest.fixture()
def fhir_client() -> MagicMock:
    client = MagicMock()
    client.update_patient = AsyncMock(return_value=(True, {"resourceType": "Patient"}, None, 5))
    return client


async def test_persist_intake_writes_every_section(
    std_client: MagicMock, fhir_client: MagicMock
) -> None:
    persister = IntakePersister(std_client=std_client, fhir_client=fhir_client)
    extraction = _intake_payload(
        medications=[{"name": "Lisinopril", "dose": "10 mg", "frequency": "daily"}],
        allergies=[{"substance": "Penicillin", "severity": "moderate"}],
        demographics={"name": "Maria Chen", "dob": "1968-03-15", "phone": "555-0101"},
    )

    summary = await persister.persist_intake(
        patient_id="patient-1", extraction=extraction
    )

    assert summary.allergy_ids == ("a-1",)
    assert summary.medication_ids == ("m-1",)
    # Chief concern is captured as a medical problem.
    assert summary.medical_problem_ids == ("p-1",)
    assert summary.demographics_updated is True
    assert summary.errors == ()

    std_client.create_allergy.assert_awaited_once()
    std_client.create_medication.assert_awaited_once()
    std_client.create_medical_problem.assert_awaited_once()
    fhir_client.update_patient.assert_awaited_once()


async def test_persist_intake_skips_empty_sections(
    std_client: MagicMock, fhir_client: MagicMock
) -> None:
    persister = IntakePersister(std_client=std_client, fhir_client=fhir_client)
    # Demographics is required by the schema but every field defaults to
    # None (the patient skipped the section entirely).
    extraction = _intake_payload()

    summary = await persister.persist_intake(
        patient_id="patient-1", extraction=extraction
    )

    # Only chief_concern → medical_problem fired.
    assert summary.allergy_ids == ()
    assert summary.medication_ids == ()
    assert summary.medical_problem_ids == ("p-1",)
    # Empty demographics block → no PUT, no error.
    assert summary.demographics_updated is False
    assert summary.errors == ()

    std_client.create_allergy.assert_not_awaited()
    std_client.create_medication.assert_not_awaited()
    fhir_client.update_patient.assert_not_awaited()


async def test_persist_intake_captures_partial_failures(
    std_client: MagicMock, fhir_client: MagicMock
) -> None:
    persister = IntakePersister(std_client=std_client, fhir_client=fhir_client)
    std_client.create_allergy = AsyncMock(return_value=_err("http_400"))
    std_client.create_medication = AsyncMock(return_value=_ok("m-1"))
    std_client.create_medical_problem = AsyncMock(return_value=_ok("p-1"))

    extraction = _intake_payload(
        medications=[{"name": "Lisinopril"}],
        allergies=[{"substance": "Penicillin"}],
    )

    summary = await persister.persist_intake(
        patient_id="patient-1", extraction=extraction
    )

    assert summary.allergy_ids == ()
    assert summary.medication_ids == ("m-1",)
    # Chief concern still wrote successfully.
    assert summary.medical_problem_ids == ("p-1",)
    assert any("Penicillin" in e for e in summary.errors)


async def test_persist_intake_demographics_failure_lands_in_errors(
    std_client: MagicMock, fhir_client: MagicMock
) -> None:
    fhir_client.update_patient = AsyncMock(return_value=(False, None, "http_404", 5))
    persister = IntakePersister(std_client=std_client, fhir_client=fhir_client)
    extraction = _intake_payload(
        demographics={"name": "Maria Chen", "dob": "1968-03-15"},
    )

    summary = await persister.persist_intake(
        patient_id="patient-1", extraction=extraction
    )

    assert summary.demographics_updated is False
    assert any("demographics" in e for e in summary.errors)


async def test_persist_intake_handles_fhir_client_without_update_patient(
    std_client: MagicMock,
) -> None:
    fhir_client = MagicMock(spec=["search", "read"])  # no update_patient
    persister = IntakePersister(std_client=std_client, fhir_client=fhir_client)
    extraction = _intake_payload(demographics={"name": "Maria Chen"})

    summary = await persister.persist_intake(
        patient_id="patient-1", extraction=extraction
    )

    assert summary.demographics_updated is False
    # Skipping when the method is absent is a soft failure: it lands in
    # errors but does not raise.
    assert any("demographics" in e for e in summary.errors)


# ---------------------------------------------------------------------------
# DocumentExtractionStore — mocked psycopg pool
# ---------------------------------------------------------------------------


class _FakePool:
    """Minimal AsyncConnectionPool stand-in for unit tests.

    Returns a single fake connection whose cursor records the executed
    SQL and returns a canned ``RETURNING id`` row.
    """

    def __init__(self, returned_id: int = 42) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.rows: list[dict[str, Any]] = []
        self._returned_id = returned_id

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def connection(self) -> Any:
        return _FakeConnCM(self)


class _FakeConnCM:
    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    async def __aenter__(self) -> Any:
        return _FakeConn(self._pool)

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FakeConn:
    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    def cursor(self) -> Any:
        return _FakeCurCM(self._pool)


class _FakeCurCM:
    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    async def __aenter__(self) -> Any:
        return _FakeCur(self._pool)

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FakeCur:
    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool
        self._last_row: tuple[int, ...] | None = None

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self._pool.executed.append((sql, params))
        if "RETURNING id" in sql:
            self._pool.rows.append(
                {
                    "id": self._pool._returned_id,
                    "document_id": params[0],
                    "patient_id": params[1],
                    "doc_type": params[2],
                    "extraction_json": params[3],
                    "bboxes_json": params[4],
                    "filename": params[5],
                    "content_sha256": params[6],
                }
            )
            self._last_row = (self._pool._returned_id,)
        elif "content_sha256 = %s" in sql:
            patient_id, filename, content_sha256 = params[:3]
            self._last_row = _row_tuple(
                next(
                    (
                        row
                        for row in reversed(self._pool.rows)
                        if row["patient_id"] == patient_id
                        and row["filename"] == filename
                        and row["content_sha256"] == content_sha256
                    ),
                    None,
                )
            )
        elif "document_id = %s" in sql:
            patient_id, document_id = params[:2]
            self._last_row = _row_tuple(
                next(
                    (
                        row
                        for row in reversed(self._pool.rows)
                        if row["patient_id"] == patient_id
                        and row["document_id"] == document_id
                    ),
                    None,
                )
            )

    async def fetchone(self) -> tuple[int, ...] | None:
        return self._last_row


@pytest.fixture()
def fake_pool() -> _FakePool:
    return _FakePool()


def _patch_pool(pool: _FakePool) -> Any:
    """Patch ``psycopg_pool.AsyncConnectionPool`` to construct ``pool``."""

    def _factory(*_args: Any, **_kwargs: Any) -> _FakePool:
        return pool

    return patch("psycopg_pool.AsyncConnectionPool", _factory)


def _row_tuple(row: dict[str, Any] | None) -> tuple[Any, ...] | None:
    if row is None:
        return None
    return (
        row["id"],
        row["document_id"],
        row["patient_id"],
        row["doc_type"],
        row["extraction_json"],
        row["bboxes_json"],
        row["filename"],
        row["content_sha256"],
    )


async def test_save_lab_extraction_inserts_and_returns_id(fake_pool: _FakePool) -> None:
    pytest.importorskip("psycopg_pool")
    store = DocumentExtractionStore(dsn="postgres://fake")
    with _patch_pool(fake_pool):
        new_id = await store.save_lab_extraction(
            extraction=_lab_payload(),
            bboxes=[],
            document_id="doc-1",
            patient_id="patient-1",
            filename="lab.pdf",
            content_sha256="sha-abc",
        )

    assert new_id == 42
    assert fake_pool.executed, "expected at least one SQL statement"
    sql, params = fake_pool.executed[-1]
    assert "INSERT INTO document_extractions" in sql
    # (document_id, patient_id, doc_type, extraction_json, bboxes_json, filename, content_sha256)
    assert params[0] == "doc-1"
    assert params[1] == "patient-1"
    assert params[2] == "lab_pdf"
    assert "results" in params[3]  # JSON-encoded extraction
    assert params[5] == "lab.pdf"
    assert params[6] == "sha-abc"


async def test_save_intake_extraction_uses_intake_doc_type(fake_pool: _FakePool) -> None:
    pytest.importorskip("psycopg_pool")
    fake_pool._returned_id = 17
    store = DocumentExtractionStore(dsn="postgres://fake")
    with _patch_pool(fake_pool):
        new_id = await store.save_intake_extraction(
            extraction=_intake_payload(),
            bboxes=[],
            document_id="doc-2",
            patient_id="patient-2",
            filename="intake.png",
            content_sha256="sha-intake",
        )

    assert new_id == 17
    _sql, params = fake_pool.executed[-1]
    assert params[2] == "intake_form"


async def test_store_hash_lookup_is_scoped_by_patient(fake_pool: _FakePool) -> None:
    pytest.importorskip("psycopg_pool")
    store = DocumentExtractionStore(dsn="postgres://fake")
    with _patch_pool(fake_pool):
        await store.save_lab_extraction(
            extraction=_lab_payload(),
            bboxes=[],
            document_id="doc-1",
            patient_id="patient-1",
            filename="lab.pdf",
            content_sha256="sha-abc",
        )
        same_patient = await store.get_latest_by_hash(
            patient_id="patient-1",
            filename="lab.pdf",
            content_sha256="sha-abc",
        )
        different_patient = await store.get_latest_by_hash(
            patient_id="patient-2",
            filename="lab.pdf",
            content_sha256="sha-abc",
        )

    assert same_patient is not None
    assert same_patient["document_id"] == "doc-1"
    assert same_patient["extraction_json"]["results"][0]["test_name"] == "LDL"
    assert different_patient is None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_split_name_preserves_first_last() -> None:
    assert _split_name("Maria Elena Chen") == ("Chen", ["Maria", "Elena"])
    assert _split_name("Plato") == ("", ["Plato"])
    assert _split_name("") == ("", [])


def test_patient_resource_returns_none_for_empty_demographics() -> None:
    demographics = IntakeDemographics()
    body = _patient_resource_from_demographics("patient-1", demographics)
    assert body is None


def test_patient_resource_includes_populated_fields_only() -> None:
    demographics = IntakeDemographics(
        name="Maria Chen",
        dob="1968-03-15",
        gender="female",
    )
    body = _patient_resource_from_demographics("patient-1", demographics)
    assert body is not None
    assert body["resourceType"] == "Patient"
    assert body["id"] == "patient-1"
    assert body["birthDate"] == "1968-03-15"
    assert body["gender"] == "female"
    assert body["name"][0]["family"] == "Chen"
    assert body["name"][0]["given"] == ["Maria"]
    # Phone / address fields not provided → not in body.
    assert "telecom" not in body
    assert "address" not in body
