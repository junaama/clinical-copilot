"""DOCX referral upload behavior (issue 005)."""

from __future__ import annotations

import io
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from copilot.extraction.docx_referral import parse_docx_referral
from copilot.tools.extraction import make_extraction_tools

ASSETS = Path(__file__).resolve().parents[2] / "cohort-5-week-2-assets-v2" / "docx"


def _asset_bytes(filename: str = "p01-chen-referral.docx") -> bytes:
    return (ASSETS / filename).read_bytes()


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
            return True, "doc-docx", None, 1

    class _StubExtractionStore:
        def __init__(self) -> None:
            self.referral_saves: list[dict[str, Any]] = []

        async def save_lab_extraction(self, **_kwargs: Any) -> int:
            raise AssertionError("DOCX referral should not save lab extraction")

        async def save_intake_extraction(self, **_kwargs: Any) -> int:
            raise AssertionError("DOCX referral should not save intake extraction")

        async def save_referral_extraction(self, **kwargs: Any) -> int:
            self.referral_saves.append(kwargs)
            return 5

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


def test_parse_real_docx_referral_asset_to_referral_extraction() -> None:
    extraction = parse_docx_referral(
        _asset_bytes(),
        document_id="DocumentReference/doc-docx",
    )

    assert extraction.referring_provider == "Helen Park, MD"
    assert extraction.referring_organization == (
        "Berkeley Health System — Internal Medicine Associates"
    )
    assert extraction.receiving_provider == "Jonathan Liu, MD, FACC"
    assert extraction.receiving_organization == "Bay Area Cardiovascular Consultants"
    assert extraction.patient_name == "Margaret Chen"
    assert extraction.patient_identifiers["MRN"] == "BHS-2847163"
    assert extraction.reason_for_referral is not None
    assert "statin-refractory hyperlipidemia" in extraction.reason_for_referral
    assert extraction.current_medications == [
        "atorvastatin 40 mg PO daily",
        "metformin 1000 mg PO BID",
        "lisinopril 20 mg PO daily",
        "aspirin 81 mg PO daily",
    ]
    assert extraction.allergies == ["NKDA"]
    assert extraction.pertinent_labs[1].name == "LDL-C"
    assert extraction.pertinent_labs[1].flag == "HIGH"
    assert extraction.pertinent_labs[1].source_citation.page_or_section == "paragraph 26"
    assert extraction.source_citations["reason_for_referral"].page_or_section == "paragraph 12"


def test_upload_docx_referral_returns_referral_payload_without_vlm(
    upload_client: TestClient,
) -> None:
    response = upload_client.post(
        "/upload",
        files={
            "file": (
                "p01-chen-referral.docx",
                io.BytesIO(_asset_bytes()),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        },
        data={
            "patient_id": "patient-chen",
            "doc_type": "docx_referral",
            "conversation_id": "conv-docx",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["doc_type"] == "docx_referral"
    assert body["lab"] is None
    assert body["intake"] is None
    assert body["referral"] is not None
    assert body["referral"]["patient_name"] == "Margaret Chen"
    assert body["referral"]["receiving_provider"] == "Jonathan Liu, MD, FACC"
    assert body["referral"]["pertinent_labs"][1]["name"] == "LDL-C"
    assert body["referral"]["pertinent_labs"][1]["source_citation"]["page_or_section"] == (
        "paragraph 26"
    )
    assert body["bboxes"] == []
    assert len(upload_client.stub_store.referral_saves) == 1  # type: ignore[attr-defined]
    assert upload_client.system_messages[0].content.startswith(  # type: ignore[attr-defined]
        "[system] Document uploaded:"
    )

    upload_client.resolve_vlm.assert_not_called()  # type: ignore[attr-defined]
    upload_client.vlm.assert_not_awaited()  # type: ignore[attr-defined]


async def test_extract_document_docx_referral_uses_deterministic_parser_without_vlm() -> None:
    from copilot.care_team import AuthDecision, CareTeamGate

    gate = MagicMock(spec=CareTeamGate)
    gate.assert_authorized = AsyncMock(return_value=AuthDecision.ALLOWED)

    document_client = MagicMock()
    document_client.upload = AsyncMock()
    document_client.list = AsyncMock()
    document_client.download = AsyncMock(
        return_value=(
            True,
            _asset_bytes("p02-whitaker-referral.docx"),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            None,
            1,
        )
    )
    store = MagicMock()
    store.get_latest_by_document_id = AsyncMock(return_value=None)
    store.get_latest_by_hash = AsyncMock(return_value=None)
    store.save_lab_extraction = AsyncMock()
    store.save_intake_extraction = AsyncMock()
    store.save_referral_extraction = AsyncMock(return_value=88)
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
                "patient_id": "patient-whitaker",
                "document_id": "doc-referral",
                "doc_type": "docx_referral",
                "filename": "p02-whitaker-referral.docx",
            }
        )

    assert result["ok"] is True
    assert result["doc_type"] == "docx_referral"
    assert result["extraction_id"] == 88
    assert result["extraction"]["receiving_provider"] == "Priya Subramanian, MD"
    requested_action_section = result["extraction"]["source_citations"][
        "requested_action"
    ]["page_or_section"]
    assert requested_action_section.startswith("paragraph ")
    assert result["bboxes"] == []
    assert result["pages_processed"] == 0
    vlm.assert_not_awaited()
    store.save_referral_extraction.assert_awaited_once()
    store.save_lab_extraction.assert_not_awaited()
    store.save_intake_extraction.assert_not_awaited()
