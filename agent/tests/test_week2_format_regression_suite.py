"""Week-2 multi-format upload regression suite.

This acceptance layer protects one externally visible ``POST /upload`` path per
required week-2 document family while keeping OpenEMR and LLM/VLM calls stubbed.
The narrower parser tests still cover parser internals; this file guards the
wire contract the UI and post-upload chat handoff depend on.
"""

from __future__ import annotations

import io
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import SystemMessage

from copilot.extraction.schemas import (
    IntakeDemographics,
    IntakeExtraction,
    LabExtraction,
    LabResult,
    SourceCitation,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
WEEK2_ASSETS = REPO_ROOT / "cohort-5-week-2-assets-v2"
EXAMPLE_DOCS = REPO_ROOT / "example-documents"
JPEG_ASSET = REPO_ROOT / "interface" / "forms" / "CAMOS" / "xout.jpg"


class _StubDocumentClient:
    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.next_document_id = "doc-week2"

    async def upload(
        self,
        patient_id: str,
        file_data: bytes,
        filename: str,
        category: str,
    ) -> tuple[bool, str | None, str | None, int]:
        self.uploads.append(
            {
                "patient_id": patient_id,
                "filename": filename,
                "category": category,
                "size": len(file_data),
            }
        )
        return True, self.next_document_id, None, 1


class _StubExtractionStore:
    def __init__(self) -> None:
        self.lab_saves: list[dict[str, Any]] = []
        self.intake_saves: list[dict[str, Any]] = []
        self.referral_saves: list[dict[str, Any]] = []
        self.adt_saves: list[dict[str, Any]] = []

    async def save_lab_extraction(self, **kwargs: Any) -> int:
        self.lab_saves.append(kwargs)
        return 11

    async def save_intake_extraction(self, **kwargs: Any) -> int:
        self.intake_saves.append(kwargs)
        return 12

    async def save_referral_extraction(self, **kwargs: Any) -> int:
        self.referral_saves.append(kwargs)
        return 13

    async def save_adt_extraction(self, **kwargs: Any) -> int:
        self.adt_saves.append(kwargs)
        return 14


def _lab_extraction(document_id: str, source_type: str) -> LabExtraction:
    return LabExtraction(
        patient_name="Margaret Chen",
        collection_date="2026-04-12",
        ordering_provider="Helen Park, MD",
        lab_name="Acceptance Lab",
        results=[
            LabResult(
                test_name="LDL Cholesterol",
                value="142",
                unit="mg/dL",
                reference_range="<100",
                collection_date="2026-04-12",
                abnormal_flag="high",
                confidence="low" if source_type == "tiff_fax" else "high",
                source_citation=SourceCitation(
                    source_type=source_type,  # type: ignore[arg-type]
                    source_id=document_id,
                    page_or_section="page 4" if source_type == "tiff_fax" else "1",
                    field_or_chunk_id="results[0].value",
                    quote_or_value="142",
                ),
            )
        ],
        source_document_id=document_id,
        extraction_model="stub-regression",
        extraction_timestamp=datetime.now(UTC).isoformat(),
    )


def _intake_extraction(document_id: str, source_type: str) -> IntakeExtraction:
    return IntakeExtraction(
        demographics=IntakeDemographics(name="Margaret Chen"),
        chief_concern="Medication follow-up",
        current_medications=[],
        allergies=[],
        family_history=[],
        social_history=None,
        source_citation=SourceCitation(
            source_type=source_type,  # type: ignore[arg-type]
            source_id=document_id,
            page_or_section="1",
            field_or_chunk_id="chief_concern",
            quote_or_value="Medication follow-up",
        ),
        source_document_id=document_id,
        extraction_model="stub-regression",
        extraction_timestamp=datetime.now(UTC).isoformat(),
    )


@pytest.fixture
def upload_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("USE_FIXTURE_FHIR", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    from copilot import server
    from copilot.extraction.vlm import ExtractionResult

    @asynccontextmanager
    async def _stub_open_checkpointer(_settings: Any):
        yield None

    async def _stub_vlm_extract_document(
        _file_data: bytes,
        _mimetype: str,
        doc_type: str,
        *,
        document_id: str,
        model: Any,
        extraction_model_name: str = "claude-sonnet-4-6",
    ) -> ExtractionResult:
        if doc_type in {"lab_pdf", "tiff_fax"}:
            extraction = _lab_extraction(document_id, doc_type)
        elif doc_type == "intake_form":
            extraction = _intake_extraction(document_id, doc_type)
        else:
            raise AssertionError(f"deterministic parser should handle {doc_type}")
        return ExtractionResult(
            ok=True,
            extraction=extraction,
            error=None,
            raw_responses=["{}"],
            pages_processed=1,
            latency_ms=1,
        )

    system_messages: list[SystemMessage] = []

    async def _stub_inject_message(*args: Any, **_kwargs: Any) -> None:
        msg = server.build_document_upload_message(
            doc_type=args[2],
            filename=args[3],
            document_id=f"DocumentReference/{args[4]}",
            patient_id=args[5],
        )
        system_messages.append(msg)

    monkeypatch.setattr(server, "open_checkpointer", _stub_open_checkpointer)
    monkeypatch.setattr(server, "build_graph", lambda *_a, **_kw: object())
    monkeypatch.setattr(server, "_vlm_extract_document", _stub_vlm_extract_document)
    monkeypatch.setattr(server, "_inject_upload_system_message", _stub_inject_message)
    monkeypatch.setattr(server, "match_extraction_to_bboxes", lambda *_a, **_kw: [])

    stub_doc = _StubDocumentClient()
    stub_store = _StubExtractionStore()

    with TestClient(server.app) as client:
        server.app.state.document_client = stub_doc
        server.app.state.extraction_store = stub_store
        server.app.state.vlm_model = object()
        client.stub_doc = stub_doc  # type: ignore[attr-defined]
        client.stub_store = stub_store  # type: ignore[attr-defined]
        client.system_messages = system_messages  # type: ignore[attr-defined]
        yield client


def _post_upload(
    client: TestClient,
    *,
    asset: Path,
    media_type: str,
    doc_type: str,
    conversation_id: str | None = None,
    confirm_doc_type: bool = False,
) -> dict[str, Any]:
    data = {"patient_id": "patient-chen", "doc_type": doc_type}
    if conversation_id is not None:
        data["conversation_id"] = conversation_id
    if confirm_doc_type:
        data["confirm_doc_type"] = "true"

    response = client.post(
        "/upload",
        files={"file": (asset.name, io.BytesIO(asset.read_bytes()), media_type)},
        data=data,
    )

    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    "label,asset,media_type,doc_type,payload_key,citation_family,field_assertion",
    [
        (
            "week-2 HL7 ORU lab",
            WEEK2_ASSETS / "hl7v2" / "p01-chen-oru-r01.hl7",
            "x-application/hl7-v2+er7",
            "hl7_oru",
            "lab",
            "hl7_oru",
            lambda body: body["lab"]["results"][1]["value"] == "142",
        ),
        (
            "week-2 HL7 ADT update",
            WEEK2_ASSETS / "hl7v2" / "p01-chen-adt-a08.hl7",
            "x-application/hl7-v2+er7",
            "hl7_adt",
            "adt",
            "hl7_adt",
            lambda body: body["adt"]["patient_demographics"]["name"] == "Margaret L Chen",
        ),
        (
            "week-2 XLSX workbook",
            WEEK2_ASSETS / "xlsx" / "p01-chen-workbook.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xlsx_workbook",
            "workbook",
            "xlsx_workbook",
            lambda body: body["workbook"]["care_gaps"][2]["status"] == "OVERDUE",
        ),
        (
            "week-2 DOCX referral",
            WEEK2_ASSETS / "docx" / "p01-chen-referral.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "docx_referral",
            "referral",
            "docx_referral",
            lambda body: body["referral"]["receiving_provider"]
            == "Jonathan Liu, MD, FACC",
        ),
        (
            "week-2 TIFF fax packet",
            WEEK2_ASSETS / "tiff" / "p01-chen-fax-packet.tiff",
            "image/tiff",
            "tiff_fax",
            "lab",
            "tiff_fax",
            lambda body: body["lab"]["results"][0]["source_citation"]["page_or_section"]
            == "page 4",
        ),
    ],
)
def test_week2_asset_families_return_visible_upload_shapes_and_citations(
    upload_client: TestClient,
    label: str,
    asset: Path,
    media_type: str,
    doc_type: str,
    payload_key: str,
    citation_family: str,
    field_assertion: Any,
) -> None:
    """Acceptance coverage for each required week-2 format family."""

    assert asset.exists(), f"{label} fixture is missing: {asset}"

    body = _post_upload(
        upload_client,
        asset=asset,
        media_type=media_type,
        doc_type=doc_type,
    )

    assert body["status"] == "ok", label
    assert body["requested_type"] == doc_type
    assert body["effective_type"] == doc_type
    assert body["document_reference"] == "DocumentReference/doc-week2"
    assert body["filename"] == asset.name
    assert body["discussable"] is True
    assert body[payload_key] is not None
    assert body["bboxes"] == []
    assert field_assertion(body), label

    citation_sources = _citation_sources(body, payload_key)
    assert citation_family in citation_sources
    assert upload_client.stub_doc.uploads[-1]["category"] == doc_type  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "label,asset,media_type,doc_type,payload_key,citation_family",
    [
        (
            "legacy PDF lab",
            EXAMPLE_DOCS / "lab-results" / "p01-chen-lipid-panel.pdf",
            "application/pdf",
            "lab_pdf",
            "lab",
            "lab_pdf",
        ),
        (
            "legacy PNG intake",
            EXAMPLE_DOCS / "intake-forms" / "p03-reyes-intake.png",
            "image/png",
            "intake_form",
            "intake",
            "intake_form",
        ),
        (
            "legacy JPEG intake",
            JPEG_ASSET,
            "image/jpeg",
            "intake_form",
            "intake",
            "intake_form",
        ),
    ],
)
def test_legacy_pdf_png_jpeg_uploads_remain_in_the_protected_suite(
    upload_client: TestClient,
    label: str,
    asset: Path,
    media_type: str,
    doc_type: str,
    payload_key: str,
    citation_family: str,
) -> None:
    assert asset.exists(), f"{label} fixture is missing: {asset}"

    body = _post_upload(
        upload_client,
        asset=asset,
        media_type=media_type,
        doc_type=doc_type,
        confirm_doc_type=True,
    )

    assert body["status"] == "ok", label
    assert body["doc_type"] == doc_type
    assert body[payload_key] is not None
    assert body["failure_reason"] is None
    assert citation_family in _citation_sources(body, payload_key)


def test_non_pdf_upload_populates_discussion_handoff_message(
    upload_client: TestClient,
) -> None:
    asset = WEEK2_ASSETS / "docx" / "p01-chen-referral.docx"

    body = _post_upload(
        upload_client,
        asset=asset,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        doc_type="docx_referral",
        conversation_id="conv-week2-docx",
    )

    assert body["status"] == "ok"
    assert body["referral"]["patient_name"] == "Margaret Chen"

    messages = upload_client.system_messages  # type: ignore[attr-defined]
    assert len(messages) == 1
    assert messages[0].content.startswith("[system] Document uploaded:")
    assert "docx_referral" in messages[0].content
    assert "DocumentReference/doc-week2" in messages[0].content
    assert "patient-chen" in messages[0].content


def test_week2_wrong_family_parse_failure_is_safe_and_not_discussable(
    upload_client: TestClient,
) -> None:
    asset = WEEK2_ASSETS / "hl7v2" / "p01-chen-oru-r01.hl7"

    body = _post_upload(
        upload_client,
        asset=asset,
        media_type="x-application/hl7-v2+er7",
        doc_type="hl7_adt",
        confirm_doc_type=True,
    )

    assert body["status"] == "extraction_failed"
    assert body["discussable"] is False
    assert body["adt"] is None
    assert body["document_reference"] == "DocumentReference/doc-week2"
    assert "not an ADT message" not in (body["failure_reason"] or "")
    assert body["failure_reason"] == (
        "We couldn't extract structured data from this document. "
        "Please retry or check the file."
    )
    assert upload_client.system_messages == []  # type: ignore[attr-defined]


def _citation_sources(body: dict[str, Any], payload_key: str) -> set[str]:
    if payload_key == "lab":
        return {
            result["source_citation"]["source_type"]
            for result in body["lab"]["results"]
            if result.get("source_citation")
        }
    if payload_key == "intake":
        return {body["intake"]["source_citation"]["source_type"]}
    if payload_key == "referral":
        return {
            citation["source_type"]
            for citation in body["referral"]["source_citations"].values()
        }
    if payload_key == "adt":
        return {citation["source_type"] for citation in body["adt"]["citations"]}
    if payload_key == "workbook":
        return {
            field["source_citation"]["source_type"]
            for field in body["workbook"]["patient_fields"].values()
        }
    raise AssertionError(f"unhandled payload key {payload_key}")
