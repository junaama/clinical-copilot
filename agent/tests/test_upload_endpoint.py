"""Tests for ``POST /upload`` (issue 011).

The upload endpoint is the agent-side glue between copilot-ui's
FileUploadWidget and the document-extraction pipeline. It accepts a
multipart form containing the file plus ``patient_id`` and ``doc_type``,
uploads to OpenEMR via :class:`DocumentClient`, runs VLM extraction,
and returns an ``UploadResponse`` shaped like the UI's
``ExtractionResponse`` interface.

These tests do NOT exercise the real DocumentClient or the real VLM —
both are stubbed via ``app.state`` overrides. Tests verify the
HTTP contract: form parsing, doc_type validation, error envelopes,
and the system-message side effect that primes the classifier on the
next ``/chat`` turn.
"""

from __future__ import annotations

import io
from contextlib import asynccontextmanager
from datetime import UTC, datetime
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

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubDocumentClient:
    """In-memory stand-in for ``DocumentClient``.

    Records the most recent upload args so tests can assert what was
    sent to OpenEMR without reaching the network.
    """

    def __init__(
        self,
        *,
        ok: bool = True,
        document_id: str = "doc-123",
        error: str | None = None,
    ) -> None:
        self.ok = ok
        self.document_id = document_id
        self.error = error
        self.uploads: list[dict[str, Any]] = []

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
                "size": len(file_data),
                "filename": filename,
                "category": category,
            }
        )
        if not self.ok:
            return False, None, self.error or "stub_upload_failed", 1
        return True, self.document_id, None, 1


def _build_lab_extraction(document_id: str) -> LabExtraction:
    return LabExtraction(
        patient_name="Eduardo Test",
        collection_date="2026-04-12",
        ordering_provider="Dr. Smith",
        lab_name="Quest",
        results=[
            LabResult(
                test_name="HbA1c",
                value="7.4",
                unit="%",
                reference_range="<5.7",
                collection_date="2026-04-12",
                abnormal_flag="high",
                confidence="high",
                source_citation=SourceCitation(
                    source_type="lab_pdf",
                    source_id=document_id,
                    page_or_section="1",
                    field_or_chunk_id="results[0].value",
                    quote_or_value="7.4",
                ),
            ),
        ],
        source_document_id=document_id,
        extraction_model="claude-sonnet-4-6",
        extraction_timestamp=datetime.now(UTC).isoformat(),
    )


def _build_intake_extraction(document_id: str) -> IntakeExtraction:
    return IntakeExtraction(
        demographics=IntakeDemographics(name="Jane Doe"),
        chief_concern="headache for 3 days",
        current_medications=[],
        allergies=[],
        family_history=[],
        social_history=None,
        source_citation=SourceCitation(
            source_type="intake_form",
            source_id=document_id,
            page_or_section="1",
            quote_or_value="headache for 3 days",
        ),
        source_document_id=document_id,
        extraction_model="claude-sonnet-4-6",
        extraction_timestamp=datetime.now(UTC).isoformat(),
    )


# ---------------------------------------------------------------------------
# Fixture client
# ---------------------------------------------------------------------------


@pytest.fixture
def upload_client(monkeypatch: pytest.MonkeyPatch):
    """Build a TestClient with stubbed DocumentClient + VLM extraction.

    Both dependencies hang off ``app.state`` so handlers resolve them at
    request time. The lifespan still runs (chat lifespan plumbing) but
    the upload-specific overrides are in place for every request.
    """

    monkeypatch.setenv("USE_FIXTURE_FHIR", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    # Don't load Anthropic — VLM is stubbed.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    from copilot import server

    @asynccontextmanager
    async def _stub_open_checkpointer(_settings):
        yield None

    monkeypatch.setattr(server, "open_checkpointer", _stub_open_checkpointer)
    # Replace build_graph with a no-op so the lifespan completes without
    # building the real LangGraph.
    monkeypatch.setattr(
        server, "build_graph", lambda *_a, **_kw: object()
    )

    stub_doc = _StubDocumentClient()
    extraction_holder: dict[str, Any] = {}
    system_messages: list[SystemMessage] = []

    async def _stub_extract(
        file_data: bytes,
        mimetype: str,
        doc_type: str,
        *,
        document_id: str,
        model: Any,
        extraction_model_name: str = "claude-sonnet-4-6",
    ):
        from copilot.extraction.vlm import ExtractionResult

        if extraction_holder.get("force_error"):
            return ExtractionResult(
                ok=False,
                extraction=None,
                error="forced",
                raw_responses=[],
                pages_processed=0,
                latency_ms=1,
            )
        if doc_type == "lab_pdf":
            extraction = _build_lab_extraction(document_id)
        else:
            extraction = _build_intake_extraction(document_id)
        return ExtractionResult(
            ok=True,
            extraction=extraction,
            error=None,
            raw_responses=["{}"],
            pages_processed=1,
            latency_ms=1,
        )

    async def _stub_inject_message(
        _app: Any,
        _conversation_id: str,
        doc_type: str,
        filename: str,
        document_id: str,
        patient_id: str,
    ) -> None:
        from copilot.supervisor.upload import build_document_upload_message

        msg = build_document_upload_message(
            doc_type=doc_type,
            filename=filename,
            document_id=f"DocumentReference/{document_id}",
            patient_id=patient_id,
        )
        system_messages.append(msg)

    monkeypatch.setattr(server, "_vlm_extract_document", _stub_extract)
    monkeypatch.setattr(server, "_inject_upload_system_message", _stub_inject_message)

    with TestClient(server.app) as client:
        server.app.state.document_client = stub_doc
        server.app.state.vlm_model = object()  # not used; extraction is stubbed
        client.stub_doc = stub_doc  # type: ignore[attr-defined]
        client.system_messages = system_messages  # type: ignore[attr-defined]
        client.extraction_holder = extraction_holder  # type: ignore[attr-defined]
        yield client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_upload_lab_pdf_returns_extraction_response(upload_client: TestClient) -> None:
    """Happy path: lab PDF uploads, extracts, returns ExtractionResponse."""

    pdf_bytes = b"%PDF-1.4\n%fake-pdf-bytes-for-test\n"
    response = upload_client.post(
        "/upload",
        files={
            "file": ("hba1c.pdf", io.BytesIO(pdf_bytes), "application/pdf"),
        },
        data={
            "patient_id": "patient-eduardo-1",
            "doc_type": "lab_pdf",
            "conversation_id": "conv-1",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["document_id"] == "doc-123"
    assert body["doc_type"] == "lab_pdf"
    assert body["filename"] == "hba1c.pdf"
    assert body["lab"] is not None
    assert body["intake"] is None
    assert body["lab"]["results"][0]["test_name"] == "HbA1c"
    # The DocumentClient saw the right call.
    upload_call = upload_client.stub_doc.uploads[0]  # type: ignore[attr-defined]
    assert upload_call["patient_id"] == "patient-eduardo-1"
    assert upload_call["filename"] == "hba1c.pdf"
    assert upload_call["category"] == "lab_pdf"


def test_upload_intake_form_returns_intake_payload(upload_client: TestClient) -> None:
    """An intake-form upload returns ``intake`` populated and ``lab`` null."""

    png_bytes = b"\x89PNG\r\n\x1a\nstub-png-bytes"
    response = upload_client.post(
        "/upload",
        files={"file": ("intake.png", io.BytesIO(png_bytes), "image/png")},
        data={
            "patient_id": "patient-9",
            "doc_type": "intake_form",
            "conversation_id": "conv-2",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["doc_type"] == "intake_form"
    assert body["intake"] is not None
    assert body["lab"] is None
    assert body["intake"]["chief_concern"] == "headache for 3 days"


def test_upload_rejects_invalid_doc_type(upload_client: TestClient) -> None:
    """An unknown doc_type returns 400 before any HTTP call to OpenEMR."""

    response = upload_client.post(
        "/upload",
        files={"file": ("x.pdf", io.BytesIO(b"%PDF-1.4\n"), "application/pdf")},
        data={
            "patient_id": "patient-1",
            "doc_type": "receipt",
            "conversation_id": "conv-3",
        },
    )
    assert response.status_code == 400
    assert "doc_type" in response.json()["detail"]
    assert upload_client.stub_doc.uploads == []  # type: ignore[attr-defined]


def test_upload_rejects_empty_patient_id(upload_client: TestClient) -> None:
    """A blank patient_id returns 400 — uploads must be patient-scoped."""

    response = upload_client.post(
        "/upload",
        files={"file": ("x.pdf", io.BytesIO(b"%PDF-1.4\n"), "application/pdf")},
        data={
            "patient_id": "",
            "doc_type": "lab_pdf",
        },
    )
    assert response.status_code == 400
    assert "patient_id" in response.json()["detail"]


def test_upload_propagates_document_client_failure(upload_client: TestClient) -> None:
    """When DocumentClient fails, the endpoint returns 502 with the error."""

    stub: _StubDocumentClient = upload_client.stub_doc  # type: ignore[attr-defined]
    stub.ok = False
    stub.error = "openemr_unauthorized"

    response = upload_client.post(
        "/upload",
        files={"file": ("x.pdf", io.BytesIO(b"%PDF-1.4\n"), "application/pdf")},
        data={"patient_id": "p-1", "doc_type": "lab_pdf"},
    )
    assert response.status_code == 502
    assert "openemr_unauthorized" in response.json()["detail"]


def test_upload_propagates_extraction_failure(upload_client: TestClient) -> None:
    """When VLM extraction fails, the endpoint returns 502 with the error."""

    upload_client.extraction_holder["force_error"] = True  # type: ignore[attr-defined]

    response = upload_client.post(
        "/upload",
        files={"file": ("x.pdf", io.BytesIO(b"%PDF-1.4\n"), "application/pdf")},
        data={"patient_id": "p-1", "doc_type": "lab_pdf"},
    )
    assert response.status_code == 502
    assert "extraction" in response.json()["detail"].lower()


def test_upload_injects_system_message_when_conversation_id_present(
    upload_client: TestClient,
) -> None:
    """A conversation_id triggers a [system] Document uploaded sentinel."""

    response = upload_client.post(
        "/upload",
        files={"file": ("hba1c.pdf", io.BytesIO(b"%PDF-1.4\n"), "application/pdf")},
        data={
            "patient_id": "p-eduardo",
            "doc_type": "lab_pdf",
            "conversation_id": "conv-abc",
        },
    )
    assert response.status_code == 200
    msgs: list[SystemMessage] = upload_client.system_messages  # type: ignore[attr-defined]
    assert len(msgs) == 1
    assert msgs[0].content.startswith("[system] Document uploaded:")
    assert "lab_pdf" in msgs[0].content
    assert "doc-123" in msgs[0].content


def test_upload_skips_system_message_when_no_conversation_id(
    upload_client: TestClient,
) -> None:
    """No conversation_id means no system-message injection."""

    response = upload_client.post(
        "/upload",
        files={"file": ("x.pdf", io.BytesIO(b"%PDF-1.4\n"), "application/pdf")},
        data={"patient_id": "p-1", "doc_type": "lab_pdf"},
    )
    assert response.status_code == 200
    msgs: list[SystemMessage] = upload_client.system_messages  # type: ignore[attr-defined]
    assert msgs == []
