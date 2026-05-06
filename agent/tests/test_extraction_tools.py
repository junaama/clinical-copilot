"""Unit tests for ``copilot.tools.extraction`` (issue 006).

Three LangChain tools wired through ``make_extraction_tools``:

* ``attach_document`` — file_path read + DocumentClient.upload
* ``list_patient_documents`` — DocumentClient.list passthrough
* ``extract_document`` — download → VLM → bbox match → persist

These tests exercise the tools through their LangChain
``StructuredTool`` surface and assert external behavior — gate
denial, doc-type validation, error envelope shape, and persistence
dispatch (lab vs. intake).

Dependencies are mocked end-to-end:

* ``CareTeamGate`` — patched at the granular level via the
  ``set_active_user_id`` contextvar combined with admin bypass, so the
  gate runs but resolves quickly without a real FHIR call.
* ``DocumentClient`` — ``MagicMock`` with ``AsyncMock`` methods.
* VLM extract / bbox matcher — patched at the
  ``copilot.tools.extraction`` namespace.
* ``DocumentExtractionStore`` / ``IntakePersister`` — ``MagicMock``
  with ``AsyncMock`` methods.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from copilot.care_team import AuthDecision, CareTeamGate
from copilot.extraction.persistence import IntakeWriteSummary
from copilot.extraction.schemas import (
    FieldWithBBox,
    IntakeAllergy,
    IntakeDemographics,
    IntakeExtraction,
    LabExtraction,
    LabResult,
    SourceCitation,
)
from copilot.tools import set_active_user_id
from copilot.tools.extraction import make_extraction_tools

# ---------------------------------------------------------------------------
# Fixtures and builders
# ---------------------------------------------------------------------------


def _allow_gate() -> CareTeamGate:
    gate = MagicMock(spec=CareTeamGate)
    gate.assert_authorized = AsyncMock(return_value=AuthDecision.ALLOWED)
    return gate


def _deny_gate(decision: AuthDecision = AuthDecision.CARETEAM_DENIED) -> CareTeamGate:
    gate = MagicMock(spec=CareTeamGate)
    gate.assert_authorized = AsyncMock(return_value=decision)
    return gate


def _document_client(
    *,
    upload: tuple[bool, str | None, str | None, int] = (True, "doc-1", None, 5),
    docs: tuple[bool, list[dict[str, Any]], str | None, int] = (
        True,
        [{"id": "doc-1", "category": "lab_pdf"}],
        None,
        5,
    ),
    download: tuple[bool, bytes | None, str | None, str | None, int] = (
        True,
        b"%PDF-fakebytes",
        "application/pdf",
        None,
        5,
    ),
) -> MagicMock:
    client = MagicMock()
    client.upload = AsyncMock(return_value=upload)
    client.list = AsyncMock(return_value=docs)
    client.download = AsyncMock(return_value=download)
    return client


def _store() -> MagicMock:
    store = MagicMock()
    store.save_lab_extraction = AsyncMock(return_value=42)
    store.save_intake_extraction = AsyncMock(return_value=17)
    return store


def _persister() -> MagicMock:
    persister = MagicMock()
    persister.persist_intake = AsyncMock(
        return_value=IntakeWriteSummary(
            allergy_ids=("a-1",),
            medication_ids=("m-1",),
            medical_problem_ids=("p-1",),
            demographics_updated=True,
            errors=(),
        )
    )
    return persister


def _lab_extraction() -> LabExtraction:
    citation = SourceCitation(
        source_type="lab_pdf",
        source_id="DocumentReference/doc-1",
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
        source_document_id="DocumentReference/doc-1",
        extraction_model="claude-sonnet-4",
        extraction_timestamp="2026-05-06T12:00:00Z",
    )


def _intake_extraction() -> IntakeExtraction:
    citation = SourceCitation(
        source_type="intake_form",
        source_id="DocumentReference/doc-1",
    )
    return IntakeExtraction(
        demographics=IntakeDemographics(name="Maria Chen"),
        chief_concern="shortness of breath",
        current_medications=[],
        allergies=[IntakeAllergy(substance="Penicillin")],
        family_history=[],
        social_history=None,
        source_citation=citation,
        source_document_id="DocumentReference/doc-1",
        extraction_model="claude-sonnet-4",
        extraction_timestamp="2026-05-06T12:00:00Z",
    )


def _build_tools(
    *,
    gate: CareTeamGate | None = None,
    document_client: MagicMock | None = None,
) -> dict[str, Any]:
    tools = make_extraction_tools(
        gate=gate or _allow_gate(),
        document_client=document_client or _document_client(),
        vlm_model=MagicMock(),
        store=_store(),
        persister=_persister(),
    )
    return {tool.name: tool for tool in tools}


@pytest.fixture(autouse=True)
def _reset_user_id() -> Any:
    # Tools call _enforce_patient_authorization which reads the
    # active user id contextvar; provide a fixture practitioner so the
    # gate's MagicMock decision is exercised.
    set_active_user_id("practitioner-test")
    yield
    set_active_user_id(None)


# ---------------------------------------------------------------------------
# attach_document
# ---------------------------------------------------------------------------


async def test_attach_document_uploads_and_returns_id(tmp_path: Path) -> None:
    file_path = tmp_path / "lab.pdf"
    file_path.write_bytes(b"%PDF-fakebytes")

    tools = _build_tools()
    result = await tools["attach_document"].ainvoke(
        {
            "patient_id": "patient-1",
            "file_path": str(file_path),
            "doc_type": "lab_pdf",
        }
    )

    assert result["ok"] is True
    assert result["document_id"] == "doc-1"
    assert result["doc_type"] == "lab_pdf"
    assert result["filename"] == "lab.pdf"
    assert result["size_bytes"] == 14


async def test_attach_document_rejects_unknown_doc_type(tmp_path: Path) -> None:
    file_path = tmp_path / "unknown.pdf"
    file_path.write_bytes(b"%PDF-")

    tools = _build_tools()
    result = await tools["attach_document"].ainvoke(
        {
            "patient_id": "patient-1",
            "file_path": str(file_path),
            "doc_type": "discharge_summary",
        }
    )

    assert result["ok"] is False
    assert "invalid doc_type" in result["error"]


async def test_attach_document_handles_missing_file(tmp_path: Path) -> None:
    tools = _build_tools()
    result = await tools["attach_document"].ainvoke(
        {
            "patient_id": "patient-1",
            "file_path": str(tmp_path / "missing.pdf"),
            "doc_type": "lab_pdf",
        }
    )

    assert result["ok"] is False
    assert "file_read_error" in result["error"]


async def test_attach_document_blocks_when_gate_denies(tmp_path: Path) -> None:
    file_path = tmp_path / "lab.pdf"
    file_path.write_bytes(b"%PDF-")

    tools = _build_tools(gate=_deny_gate())
    result = await tools["attach_document"].ainvoke(
        {
            "patient_id": "patient-out-of-team",
            "file_path": str(file_path),
            "doc_type": "lab_pdf",
        }
    )

    assert result["ok"] is False
    assert result["error"] == AuthDecision.CARETEAM_DENIED.value


async def test_attach_document_propagates_upload_failure(tmp_path: Path) -> None:
    file_path = tmp_path / "lab.pdf"
    file_path.write_bytes(b"%PDF-")

    document_client = _document_client(upload=(False, None, "http_413", 5))
    tools = _build_tools(document_client=document_client)
    result = await tools["attach_document"].ainvoke(
        {
            "patient_id": "patient-1",
            "file_path": str(file_path),
            "doc_type": "lab_pdf",
        }
    )

    assert result["ok"] is False
    assert result["error"] == "http_413"


# ---------------------------------------------------------------------------
# list_patient_documents
# ---------------------------------------------------------------------------


async def test_list_patient_documents_returns_passthrough() -> None:
    tools = _build_tools()
    result = await tools["list_patient_documents"].ainvoke({"patient_id": "patient-1"})

    assert result["ok"] is True
    assert result["count"] == 1
    assert result["documents"][0]["id"] == "doc-1"


async def test_list_patient_documents_blocks_when_gate_denies() -> None:
    tools = _build_tools(gate=_deny_gate(AuthDecision.NO_ACTIVE_PATIENT))
    result = await tools["list_patient_documents"].ainvoke({"patient_id": ""})

    assert result["ok"] is False
    assert result["error"] == AuthDecision.NO_ACTIVE_PATIENT.value


async def test_list_patient_documents_surfaces_http_failures() -> None:
    document_client = _document_client(docs=(False, [], "http_403", 5))
    tools = _build_tools(document_client=document_client)
    result = await tools["list_patient_documents"].ainvoke({"patient_id": "patient-1"})

    assert result["ok"] is False
    assert result["error"] == "http_403"


# ---------------------------------------------------------------------------
# extract_document — full pipeline
# ---------------------------------------------------------------------------


def _vlm_success(extraction: Any) -> AsyncMock:
    """Build a stub for ``vlm_extract_document`` that returns a passing result."""
    return AsyncMock(
        return_value=MagicMock(
            ok=True,
            extraction=extraction,
            error=None,
            raw_responses=[""],
            pages_processed=1,
            latency_ms=10,
        )
    )


def _vlm_failure(error: str) -> AsyncMock:
    return AsyncMock(
        return_value=MagicMock(
            ok=False,
            extraction=None,
            error=error,
            raw_responses=[],
            pages_processed=0,
            latency_ms=10,
        )
    )


def _bboxes_passthrough() -> Any:
    return MagicMock(return_value=[])


async def test_extract_document_lab_persists_to_store_and_returns_envelope() -> None:
    tools = _build_tools()
    extraction = _lab_extraction()

    with (
        patch(
            "copilot.tools.extraction.vlm_extract_document",
            _vlm_success(extraction),
        ),
        patch(
            "copilot.tools.extraction.match_extraction_to_bboxes",
            _bboxes_passthrough(),
        ),
    ):
        result = await tools["extract_document"].ainvoke(
            {
                "patient_id": "patient-1",
                "document_id": "doc-1",
                "doc_type": "lab_pdf",
            }
        )

    assert result["ok"] is True
    assert result["doc_type"] == "lab_pdf"
    assert result["extraction_id"] == 42
    assert result["intake_summary"] is None
    assert result["extraction"]["results"][0]["test_name"] == "LDL"


async def test_extract_document_intake_writes_intake_and_logs_summary() -> None:
    persister = _persister()
    store = _store()
    tools_factory_kwargs = {
        "gate": _allow_gate(),
        "document_client": _document_client(),
        "vlm_model": MagicMock(),
        "store": store,
        "persister": persister,
    }
    tools = {t.name: t for t in make_extraction_tools(**tools_factory_kwargs)}

    extraction = _intake_extraction()
    with (
        patch(
            "copilot.tools.extraction.vlm_extract_document",
            _vlm_success(extraction),
        ),
        patch(
            "copilot.tools.extraction.match_extraction_to_bboxes",
            _bboxes_passthrough(),
        ),
    ):
        result = await tools["extract_document"].ainvoke(
            {
                "patient_id": "patient-1",
                "document_id": "doc-2",
                "doc_type": "intake_form",
            }
        )

    assert result["ok"] is True
    assert result["doc_type"] == "intake_form"
    assert result["extraction_id"] == 17
    assert result["intake_summary"]["allergy_ids"] == ["a-1"]

    persister.persist_intake.assert_awaited_once()
    store.save_intake_extraction.assert_awaited_once()
    store.save_lab_extraction.assert_not_awaited()


async def test_extract_document_invalid_doc_type_returns_error() -> None:
    tools = _build_tools()
    result = await tools["extract_document"].ainvoke(
        {
            "patient_id": "patient-1",
            "document_id": "doc-1",
            "doc_type": "discharge_summary",
        }
    )

    assert result["ok"] is False
    assert "invalid doc_type" in result["error"]


async def test_extract_document_blocks_when_gate_denies() -> None:
    tools = _build_tools(gate=_deny_gate())
    result = await tools["extract_document"].ainvoke(
        {
            "patient_id": "patient-out-of-team",
            "document_id": "doc-1",
            "doc_type": "lab_pdf",
        }
    )

    assert result["ok"] is False
    assert result["error"] == AuthDecision.CARETEAM_DENIED.value


async def test_extract_document_handles_download_failure() -> None:
    document_client = _document_client(
        download=(False, None, None, "http_404", 5),
    )
    tools = _build_tools(document_client=document_client)
    result = await tools["extract_document"].ainvoke(
        {
            "patient_id": "patient-1",
            "document_id": "doc-1",
            "doc_type": "lab_pdf",
        }
    )

    assert result["ok"] is False
    assert result["error"] == "http_404"


async def test_extract_document_handles_vlm_failure() -> None:
    tools = _build_tools()

    with (
        patch(
            "copilot.tools.extraction.vlm_extract_document",
            _vlm_failure("schema invalid"),
        ),
        patch(
            "copilot.tools.extraction.match_extraction_to_bboxes",
            _bboxes_passthrough(),
        ),
    ):
        result = await tools["extract_document"].ainvoke(
            {
                "patient_id": "patient-1",
                "document_id": "doc-1",
                "doc_type": "lab_pdf",
            }
        )

    assert result["ok"] is False
    assert "vlm_extraction_failed" in result["error"]


async def test_extract_document_handles_persistence_failure() -> None:
    store = _store()
    store.save_lab_extraction = AsyncMock(side_effect=RuntimeError("db down"))
    tools = {
        t.name: t
        for t in make_extraction_tools(
            gate=_allow_gate(),
            document_client=_document_client(),
            vlm_model=MagicMock(),
            store=store,
            persister=_persister(),
        )
    }

    with (
        patch(
            "copilot.tools.extraction.vlm_extract_document",
            _vlm_success(_lab_extraction()),
        ),
        patch(
            "copilot.tools.extraction.match_extraction_to_bboxes",
            _bboxes_passthrough(),
        ),
    ):
        result = await tools["extract_document"].ainvoke(
            {
                "patient_id": "patient-1",
                "document_id": "doc-1",
                "doc_type": "lab_pdf",
            }
        )

    assert result["ok"] is False
    assert "persistence_failed" in result["error"]


async def test_extract_document_includes_bboxes_in_envelope() -> None:
    extraction = _lab_extraction()
    bbox = FieldWithBBox(
        field_path="results[0].value",
        extracted_value="180",
        matched_text="180",
        bbox=None,
        match_confidence=0.0,
    )
    tools = _build_tools()

    with (
        patch(
            "copilot.tools.extraction.vlm_extract_document",
            _vlm_success(extraction),
        ),
        patch(
            "copilot.tools.extraction.match_extraction_to_bboxes",
            MagicMock(return_value=[bbox]),
        ),
    ):
        result = await tools["extract_document"].ainvoke(
            {
                "patient_id": "patient-1",
                "document_id": "doc-1",
                "doc_type": "lab_pdf",
            }
        )

    assert result["ok"] is True
    assert len(result["bboxes"]) == 1
    assert result["bboxes"][0]["field_path"] == "results[0].value"


# ---------------------------------------------------------------------------
# Document-ref payload (issue 009)
#
# The supervisor's intake_extractor worker scrapes ``document_ref`` JSON
# keys from tool messages to populate ``fetched_refs``. The verifier
# then validates ``<cite ref="DocumentReference/..."/>`` tags against
# that set. attach_document, list_patient_documents, and extract_document
# must each emit ``document_ref`` keys on success so the wiring works
# end to end.
# ---------------------------------------------------------------------------


async def test_attach_document_emits_document_ref(tmp_path: Path) -> None:
    file_path = tmp_path / "lab.pdf"
    file_path.write_bytes(b"%PDF-1.4 stub")
    tools = _build_tools()
    result = await tools["attach_document"].ainvoke(
        {
            "patient_id": "patient-1",
            "file_path": str(file_path),
            "doc_type": "lab_pdf",
        }
    )

    assert result["document_ref"] == "DocumentReference/doc-1"


async def test_list_patient_documents_emits_document_ref_per_row() -> None:
    tools = _build_tools()
    result = await tools["list_patient_documents"].ainvoke({"patient_id": "patient-1"})

    assert result["documents"][0]["document_ref"] == "DocumentReference/doc-1"


async def test_extract_document_emits_document_ref() -> None:
    tools = _build_tools()
    extraction = _lab_extraction()
    with (
        patch(
            "copilot.tools.extraction.vlm_extract_document",
            _vlm_success(extraction),
        ),
        patch(
            "copilot.tools.extraction.match_extraction_to_bboxes",
            _bboxes_passthrough(),
        ),
    ):
        result = await tools["extract_document"].ainvoke(
            {
                "patient_id": "patient-1",
                "document_id": "doc-1",
                "doc_type": "lab_pdf",
            }
        )

    assert result["document_ref"] == "DocumentReference/doc-1"
