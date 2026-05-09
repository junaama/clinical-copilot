"""XLSX clinical workbook upload behavior (issue 004)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from copilot.extraction.xlsx_workbook import parse_xlsx_workbook
from copilot.tools.extraction import make_extraction_tools

ASSETS = Path(__file__).resolve().parents[2] / "cohort-5-week-2-assets-v2" / "xlsx"


def _asset_bytes(filename: str = "p01-chen-workbook.xlsx") -> bytes:
    return (ASSETS / filename).read_bytes()


def _lab_result_at_cell(results: list[Any], cell_ref: str) -> Any:
    return next(
        result
        for result in results
        if result.source_citation.field_or_chunk_id == cell_ref
    )


def _lab_payload_at_cell(results: list[dict[str, Any]], cell_ref: str) -> dict[str, Any]:
    return next(
        result
        for result in results
        if result["source_citation"]["field_or_chunk_id"] == cell_ref
    )


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
            return True, "doc-xlsx", None, 1

    class _StubExtractionStore:
        def __init__(self) -> None:
            self.lab_saves: list[dict[str, Any]] = []

        async def save_lab_extraction(self, **kwargs: Any) -> int:
            self.lab_saves.append(kwargs)
            return 1

        async def save_intake_extraction(self, **kwargs: Any) -> int:
            raise AssertionError("XLSX workbook should not save intake extraction")

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


def test_parse_real_workbook_to_structured_sections_and_lab_output() -> None:
    workbook, lab = parse_xlsx_workbook(
        _asset_bytes(),
        document_id="DocumentReference/doc-xlsx",
    )

    assert workbook.patient_fields["Name"].value == "Margaret Chen"
    assert workbook.patient_fields["MRN"].source_citation.field_or_chunk_id == "B5"
    assert workbook.sheet_roles == {
        "patient": "Patient",
        "medications": "Medications",
        "lab_trends": "Labs_Trend",
        "care_gaps": "Care_Gaps",
    }

    medication = workbook.medications[0]
    assert medication.brand == "Lipitor"
    assert medication.generic == "atorvastatin"
    assert medication.strength == "40 mg"
    assert medication.route == "PO"
    assert medication.sig == "1 tab PO daily"
    assert medication.indication == "Hyperlipidemia"
    assert medication.last_filled == "2026-04-10"
    assert medication.refills_remaining == "3"
    assert medication.source_citation.page_or_section == "Medications"
    assert medication.source_citation.field_or_chunk_id == "A2:J2"

    ldl_trend = workbook.lab_trends[1]
    assert ldl_trend.test_name == "LDL cholesterol (calc)"
    assert ldl_trend.values[-1].collection_date == "2026-04-12"
    assert ldl_trend.values[-1].value == "142"
    assert ldl_trend.values[-1].source_citation.field_or_chunk_id == "H3"

    gap = workbook.care_gaps[2]
    assert gap.measure == "Diabetic eye exam (annual)"
    assert gap.status == "OVERDUE"
    assert gap.due_date == "2025-10-30"
    assert gap.source_citation.field_or_chunk_id == "A4:F4"

    assert lab.source_document_id == "DocumentReference/doc-xlsx"
    assert lab.patient_name == "Margaret Chen"
    assert len(lab.results) == 16
    latest_ldl = _lab_result_at_cell(lab.results, "H3")
    assert latest_ldl.test_name == "LDL cholesterol (calc)"
    assert latest_ldl.value == "142"
    assert latest_ldl.collection_date == "2026-04-12"
    assert latest_ldl.abnormal_flag == "high"
    assert latest_ldl.source_citation.source_type == "xlsx_workbook"
    assert latest_ldl.source_citation.page_or_section == "Labs_Trend"


def test_upload_xlsx_workbook_returns_workbook_and_lab_payload_without_vlm(
    upload_client: TestClient,
) -> None:
    response = upload_client.post(
        "/upload",
        files={
            "file": (
                "p01-chen-workbook.xlsx",
                io.BytesIO(_asset_bytes()),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ),
        },
        data={
            "patient_id": "patient-chen",
            "doc_type": "xlsx_workbook",
            "conversation_id": "conv-xlsx",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["doc_type"] == "xlsx_workbook"
    assert body["workbook"]["patient_fields"]["MRN"]["value"] == "BHS-2847163"
    assert body["workbook"]["medications"][0]["generic"] == "atorvastatin"
    assert body["workbook"]["care_gaps"][2]["status"] == "OVERDUE"
    assert body["lab"] is not None
    latest_ldl = _lab_payload_at_cell(body["lab"]["results"], "H3")
    assert latest_ldl["test_name"] == "LDL cholesterol (calc)"
    assert latest_ldl["value"] == "142"
    assert body["intake"] is None
    assert body["bboxes"] == []
    assert len(upload_client.stub_store.lab_saves) == 1  # type: ignore[attr-defined]
    assert upload_client.stub_store.lab_saves[0]["doc_type"] == "xlsx_workbook"  # type: ignore[attr-defined]
    assert upload_client.system_messages[0].content.startswith(  # type: ignore[attr-defined]
        "[system] Document uploaded:"
    )

    upload_client.resolve_vlm.assert_not_called()  # type: ignore[attr-defined]
    upload_client.vlm.assert_not_awaited()  # type: ignore[attr-defined]


async def test_extract_document_xlsx_workbook_uses_deterministic_parser_without_vlm() -> None:
    from copilot.care_team import AuthDecision, CareTeamGate

    gate = MagicMock(spec=CareTeamGate)
    gate.assert_authorized = AsyncMock(return_value=AuthDecision.ALLOWED)

    document_client = MagicMock()
    document_client.upload = AsyncMock()
    document_client.list = AsyncMock()
    document_client.download = AsyncMock(
        return_value=(
            True,
            _asset_bytes("p01-chen-workbook.xlsx"),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            None,
            1,
        )
    )
    store = MagicMock()
    store.get_latest_by_document_id = AsyncMock(return_value=None)
    store.get_latest_by_hash = AsyncMock(return_value=None)
    store.save_lab_extraction = AsyncMock(return_value=88)
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
                "patient_id": "patient-chen",
                "document_id": "doc-xlsx",
                "doc_type": "xlsx_workbook",
                "filename": "p01-chen-workbook.xlsx",
            }
        )

    assert result["ok"] is True
    assert result["doc_type"] == "xlsx_workbook"
    assert result["extraction_id"] == 88
    assert result["workbook"]["care_gaps"][2]["measure"] == "Diabetic eye exam (annual)"
    latest_ldl = _lab_payload_at_cell(result["extraction"]["results"], "H3")
    assert latest_ldl["value"] == "142"
    assert result["bboxes"] == []
    vlm.assert_not_awaited()
    store.save_lab_extraction.assert_awaited_once()
    assert store.save_lab_extraction.await_args.kwargs["doc_type"] == "xlsx_workbook"
