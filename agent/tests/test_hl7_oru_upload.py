"""HL7 ORU lab upload behavior (issue 002)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from copilot.extraction.hl7_oru import parse_hl7_oru_lab
from copilot.tools.extraction import make_extraction_tools

ASSETS = Path(__file__).resolve().parents[2] / "cohort-5-week-2-assets-v2" / "hl7v2"


def _asset_bytes(filename: str = "p01-chen-oru-r01.hl7") -> bytes:
    return (ASSETS / filename).read_bytes()


@pytest.fixture
def upload_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("USE_FIXTURE_FHIR", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    from contextlib import asynccontextmanager

    from copilot import server

    @asynccontextmanager
    async def _stub_open_checkpointer(_settings: Any):
        yield None

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
                    "file_data": file_data,
                    "filename": filename,
                    "category": category,
                }
            )
            return True, "doc-hl7", None, 1

    class _StubExtractionStore:
        def __init__(self) -> None:
            self.lab_saves: list[dict[str, Any]] = []

        async def save_lab_extraction(self, **kwargs: Any) -> int:
            self.lab_saves.append(kwargs)
            return 1

        async def save_intake_extraction(self, **kwargs: Any) -> int:
            raise AssertionError("HL7 ORU should not save intake extraction")

    system_messages: list[Any] = []

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
    monkeypatch.setattr(server, "_inject_upload_system_message", _stub_inject_message)
    vlm = AsyncMock()
    resolve_vlm = MagicMock(return_value=object())
    monkeypatch.setattr(server, "_vlm_extract_document", vlm)
    monkeypatch.setattr(server, "_resolve_upload_vlm_model", resolve_vlm)

    stub_doc = _StubDocumentClient()
    stub_store = _StubExtractionStore()
    with TestClient(server.app) as client:
        server.app.state.document_client = stub_doc
        server.app.state.extraction_store = stub_store
        client.stub_doc = stub_doc  # type: ignore[attr-defined]
        client.stub_store = stub_store  # type: ignore[attr-defined]
        client.system_messages = system_messages  # type: ignore[attr-defined]
        client.vlm = vlm  # type: ignore[attr-defined]
        client.resolve_vlm = resolve_vlm  # type: ignore[attr-defined]
        yield client


def test_parse_real_oru_asset_to_lab_extraction() -> None:
    extraction = parse_hl7_oru_lab(
        _asset_bytes(),
        document_id="DocumentReference/doc-hl7",
    )

    assert extraction.patient_name == "Margaret L Chen"
    assert extraction.collection_date == "2026-04-12T09:30:00"
    assert extraction.order_context is not None
    assert extraction.order_context["placer_order_number"] == "ORD-p01-0001"
    assert extraction.order_context["universal_service_id"] == "57698-3"
    assert extraction.order_context["universal_service_text"] == (
        "Lipid panel with direct LDL - Serum or Plasma"
    )
    assert extraction.notes == [
        {
            "segment": "NTE",
            "set_id": "1",
            "source": "L",
            "comment": "Repeat lipid panel in 6 weeks after intensification of therapy.",
        }
    ]

    ldl = extraction.results[1]
    assert ldl.test_name == "Cholesterol in LDL [Mass/volume] in Serum or Plasma by Direct assay"
    assert ldl.loinc_code == "2089-1"
    assert ldl.value == "142"
    assert ldl.unit == "mg/dL"
    assert ldl.reference_range == "<100"
    assert ldl.abnormal_flag == "high"
    assert ldl.status == "F"
    assert ldl.collection_date == "2026-04-12T09:30:00"
    assert ldl.source_citation.source_type == "hl7_oru"
    assert ldl.source_citation.page_or_section == "OBX[2]"
    assert ldl.source_citation.field_or_chunk_id == "OBX-5"
    assert ldl.source_citation.quote_or_value == "142"


def test_upload_hl7_oru_returns_lab_payload_without_vlm(upload_client: TestClient) -> None:
    response = upload_client.post(
        "/upload",
        files={
            "file": (
                "p01-chen-oru-r01.hl7",
                io.BytesIO(_asset_bytes()),
                "x-application/hl7-v2+er7",
            ),
        },
        data={
            "patient_id": "patient-chen",
            "doc_type": "hl7_oru",
            "conversation_id": "conv-hl7",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["doc_type"] == "hl7_oru"
    assert body["lab"] is not None
    assert body["intake"] is None
    assert body["bboxes"] == []
    assert body["lab"]["patient_identifiers"][0]["id"] == "BHS-2847163"
    assert body["lab"]["results"][0]["loinc_code"] == "2093-3"
    assert body["lab"]["results"][0]["source_citation"]["page_or_section"] == "OBX[1]"
    assert body["lab"]["notes"][0]["segment"] == "NTE"
    assert len(upload_client.stub_store.lab_saves) == 1  # type: ignore[attr-defined]
    assert upload_client.stub_store.lab_saves[0]["bboxes"] == []  # type: ignore[attr-defined]
    assert upload_client.system_messages[0].content.startswith(  # type: ignore[attr-defined]
        "[system] Document uploaded:"
    )

    upload_client.resolve_vlm.assert_not_called()  # type: ignore[attr-defined]
    upload_client.vlm.assert_not_awaited()  # type: ignore[attr-defined]


async def test_extract_document_hl7_oru_uses_deterministic_parser_without_vlm() -> None:
    from copilot.care_team import AuthDecision, CareTeamGate

    gate = MagicMock(spec=CareTeamGate)
    gate.assert_authorized = AsyncMock(return_value=AuthDecision.ALLOWED)

    document_client = MagicMock()
    document_client.upload = AsyncMock()
    document_client.list = AsyncMock()
    document_client.download = AsyncMock(
        return_value=(
            True,
            _asset_bytes("p06-johnson-oru-r01.hl7"),
            "x-application/hl7-v2+er7",
            None,
            1,
        )
    )
    store = MagicMock()
    store.get_latest_by_document_id = AsyncMock(return_value=None)
    store.get_latest_by_hash = AsyncMock(return_value=None)
    store.save_lab_extraction = AsyncMock(return_value=77)
    store.save_intake_extraction = AsyncMock()
    persister = MagicMock()
    persister.persist_intake = AsyncMock()
    vlm = AsyncMock()

    tools = {
        tool.name: tool
        for tool in make_extraction_tools(
            gate=gate,
            document_client=document_client,
            vlm_model=MagicMock(),
            store=store,
            persister=persister,
        )
    }

    with patch("copilot.tools.extraction.vlm_extract_document", vlm):
        result = await tools["extract_document"].ainvoke(
            {
                "patient_id": "patient-johnson",
                "document_id": "doc-oru",
                "doc_type": "hl7_oru",
                "filename": "p06-johnson-oru-r01.hl7",
            }
        )

    assert result["ok"] is True
    assert result["doc_type"] == "hl7_oru"
    assert result["extraction_id"] == 77
    assert result["extraction"]["results"][0]["loinc_code"] == "2951-2"
    assert result["extraction"]["results"][-1]["abnormal_flag"] == "low"
    assert result["bboxes"] == []
    vlm.assert_not_awaited()
    store.save_lab_extraction.assert_awaited_once()
