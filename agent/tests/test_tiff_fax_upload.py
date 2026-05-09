"""End-to-end upload coverage for multipage TIFF fax packets."""

from __future__ import annotations

import io
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from copilot.extraction.schemas import LabExtraction, LabResult, SourceCitation

ASSETS = Path(__file__).resolve().parents[2] / "cohort-5-week-2-assets-v2" / "tiff"
FAX_PACKET = ASSETS / "p01-chen-fax-packet.tiff"


class _StubReply:
    def __init__(self, *, parsed: Any) -> None:
        self._parsed = parsed

    def as_dict(self) -> dict[str, Any]:
        class _Raw:
            content = "{}"

        return {"raw": _Raw(), "parsed": self._parsed, "parsing_error": None}


class _StubVisionModel:
    def __init__(self, replies: list[_StubReply]) -> None:
        self._replies = list(replies)
        self.invocations: list[Any] = []

    def with_structured_output(
        self, _schema: Any, *, include_raw: bool = False
    ) -> _StubVisionModel:
        assert include_raw is True
        return self

    async def ainvoke(self, messages: Any, **_kwargs: Any) -> Any:
        self.invocations.append(messages)
        if not self._replies:
            raise RuntimeError("StubVisionModel exhausted")
        return self._replies.pop(0).as_dict()


class _StubDocumentClient:
    def __init__(self) -> None:
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
                "filename": filename,
                "category": category,
                "size": len(file_data),
            }
        )
        return True, "fax-doc-1", None, 1


class _StubExtractionStore:
    def __init__(self) -> None:
        self.lab_saves: list[dict[str, Any]] = []

    async def save_lab_extraction(self, **kwargs: Any) -> int:
        self.lab_saves.append(kwargs)
        return 101


def _empty_lab_page() -> LabExtraction:
    return LabExtraction(
        patient_name=None,
        results=[],
        source_document_id="DocumentReference/fax-doc-1",
        extraction_model="stub",
        extraction_timestamp=datetime.now(UTC).isoformat(),
    )


def _lab_report_page() -> LabExtraction:
    return LabExtraction(
        patient_name="Margaret Chen",
        collection_date="2026-04-12",
        ordering_provider="Dr. Helen Park, MD",
        lab_name="Quest Diagnostics",
        results=[
            LabResult(
                test_name="LDL Cholesterol",
                value="142",
                unit="mg/dL",
                reference_range="<100",
                collection_date="2026-04-12",
                abnormal_flag="high",
                confidence="low",
                source_citation=SourceCitation(
                    source_type="lab_pdf",
                    source_id="DocumentReference/wrong",
                    page_or_section=None,
                    field_or_chunk_id="results[0].value",
                    quote_or_value="142",
                ),
            ),
        ],
        source_document_id="DocumentReference/fax-doc-1",
        extraction_model="stub",
        extraction_timestamp=datetime.now(UTC).isoformat(),
    )


@pytest.fixture
def upload_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("USE_FIXTURE_FHIR", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    from copilot import server

    @asynccontextmanager
    async def _stub_open_checkpointer(_settings: Any):
        yield None

    monkeypatch.setattr(server, "open_checkpointer", _stub_open_checkpointer)
    monkeypatch.setattr(server, "build_graph", lambda *_a, **_kw: object())
    monkeypatch.setattr(
        server,
        "_inject_upload_system_message",
        lambda *_a, **_kw: None,
    )

    stub_doc = _StubDocumentClient()
    stub_store = _StubExtractionStore()
    stub_model = _StubVisionModel(
        [
            _StubReply(parsed=_empty_lab_page()),
            _StubReply(parsed=_empty_lab_page()),
            _StubReply(parsed=_empty_lab_page()),
            _StubReply(parsed=_lab_report_page()),
        ]
    )

    with TestClient(server.app) as client:
        server.app.state.document_client = stub_doc
        server.app.state.extraction_store = stub_store
        server.app.state.vlm_model = stub_model
        client.stub_doc = stub_doc  # type: ignore[attr-defined]
        client.stub_store = stub_store  # type: ignore[attr-defined]
        client.stub_model = stub_model  # type: ignore[attr-defined]
        yield client


@pytest.mark.skipif(not FAX_PACKET.exists(), reason="fixture TIFF missing")
def test_upload_real_tiff_fax_packet_extracts_page_aware_low_confidence_lab(
    upload_client: TestClient,
) -> None:
    response = upload_client.post(
        "/upload",
        files={
            "file": (
                FAX_PACKET.name,
                io.BytesIO(FAX_PACKET.read_bytes()),
                "image/tiff",
            )
        },
        data={"patient_id": "patient-1", "doc_type": "tiff_fax"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["doc_type"] == "tiff_fax"
    assert body["lab"] is not None
    assert body["lab"]["results"][0]["confidence"] == "low"
    citation = body["lab"]["results"][0]["source_citation"]
    assert citation["source_type"] == "tiff_fax"
    assert citation["source_id"] == "DocumentReference/fax-doc-1"
    assert citation["page_or_section"] == "page 4"
    assert len(upload_client.stub_model.invocations) == 4  # type: ignore[attr-defined]
    assert upload_client.stub_doc.uploads[0]["category"] == "tiff_fax"  # type: ignore[attr-defined]
    assert upload_client.stub_store.lab_saves[0]["doc_type"] == "tiff_fax"  # type: ignore[attr-defined]
