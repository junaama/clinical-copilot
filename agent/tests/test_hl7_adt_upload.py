"""HL7 ADT upload + parse behavior (issue 003)."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from copilot.extraction.hl7_adt import parse_hl7_adt

ASSETS = Path(__file__).resolve().parents[2] / "cohort-5-week-2-assets-v2" / "hl7v2"


def _asset_bytes(filename: str) -> bytes:
    return (ASSETS / filename).read_bytes()


# ---------------------------------------------------------------------------
# Deterministic parser
# ---------------------------------------------------------------------------


class TestParseHl7Adt:
    """parse_hl7_adt extracts the structured ADT fields callers depend on."""

    def test_parses_message_metadata(self) -> None:
        extraction = parse_hl7_adt(
            _asset_bytes("p01-chen-adt-a08.hl7"),
            document_id="DocumentReference/doc-adt-1",
        )
        meta = extraction.message_metadata
        assert meta.message_type == "ADT"
        assert meta.trigger_event == "A08"
        assert meta.message_structure == "ADT_A01"
        assert meta.sending_facility == "BERKELEY HLTH SYS"
        assert meta.message_control_id == "MSG-p01-20260506143215-ADT"
        assert meta.message_datetime == "2026-05-06T14:32:15"
        assert meta.event_type == "A08"
        assert meta.event_datetime == "2026-05-06T14:32:15"
        assert (
            meta.event_reason
            == "Medication change recorded — atorvastatin titration; ezetimibe added"
        )
        assert meta.processing_id == "P"
        assert meta.version == "2.5.1"

    def test_parses_patient_demographics_and_identifiers(self) -> None:
        extraction = parse_hl7_adt(
            _asset_bytes("p01-chen-adt-a08.hl7"),
            document_id="DocumentReference/doc-adt-1",
        )
        assert extraction.patient_identifiers == [
            {
                "id": "BHS-2847163",
                "assigning_authority": "MRN",
                "type": "MR",
            }
        ]
        demo = extraction.patient_demographics
        assert demo.name == "Margaret L Chen"
        assert demo.dob == "1968-03-12T00:00:00"
        assert demo.gender == "F"
        assert demo.race == "Asian"
        assert demo.marital_status == "M"
        assert demo.address == "2418 CHANNING WAY, BERKELEY, CA 94704, USA"
        assert demo.phone == "(510) 555-0142"

    def test_parses_visit_primary_care_and_emergency_contact(self) -> None:
        extraction = parse_hl7_adt(
            _asset_bytes("p01-chen-adt-a08.hl7"),
            document_id="DocumentReference/doc-adt-1",
        )
        assert extraction.primary_care is not None
        assert extraction.primary_care.patient_primary_facility == "BERKELEY HLTH SYS LAB"
        assert extraction.primary_care.patient_primary_care_provider == "Helen M Park"
        assert extraction.visit is not None
        assert extraction.visit.patient_class == "O"
        assert extraction.visit.location == "BHS IM CLINIC - BERKELEY HEALTH"
        assert extraction.visit.attending_provider == "Helen M Park"
        assert extraction.contacts == [
            type(extraction.contacts[0])(
                name="David Chen",
                relationship="SPO",
                phone=None,
                address=None,
            )
        ]

    def test_parses_guarantor_and_insurance_from_fixture_layout(self) -> None:
        extraction = parse_hl7_adt(
            _asset_bytes("p01-chen-adt-a08.hl7"),
            document_id="DocumentReference/doc-adt-1",
        )
        assert extraction.guarantor is not None
        assert extraction.guarantor.name == "Margaret L Chen"
        assert extraction.guarantor.address == (
            "2418 CHANNING WAY, BERKELEY, CA 94704, USA"
        )
        assert extraction.guarantor.phone == "(510) 555-0142"
        assert extraction.guarantor.relationship_to_patient == "SEL"
        assert len(extraction.insurance) == 1
        plan = extraction.insurance[0]
        assert plan.company_name == "BLUE SHIELD OF CALIFORNIA PPO"
        assert plan.plan_id == "BSCA001"
        assert plan.member_id == "XEH123456789"
        assert plan.plan_type == "PPO"
        assert plan.insured_name == "Margaret L Chen"
        assert plan.relationship_to_subscriber == "SEL"

    def test_parses_al1_allergies_when_present(self) -> None:
        extraction = parse_hl7_adt(
            _asset_bytes("p06-johnson-adt-a08.hl7"),
            document_id="DocumentReference/doc-adt-6",
        )
        substances = [allergy.substance for allergy in extraction.allergies]
        assert substances == ["LISINOPRIL", "ACE INHIBITORS"]
        assert extraction.allergies[0].type == "DA"
        assert extraction.allergies[0].severity == "SV"
        assert extraction.allergies[0].reaction == "Angioedema"

    def test_emits_segment_aware_citations(self) -> None:
        extraction = parse_hl7_adt(
            _asset_bytes("p01-chen-adt-a08.hl7"),
            document_id="DocumentReference/doc-adt-1",
        )
        segments_by_field = {
            (citation.segment, citation.field) for citation in extraction.citations
        }
        # Each populated section must produce a citation that names the
        # HL7 segment + field. Without these the verifier can't audit a
        # demographic claim back to the message.
        assert ("MSH", "MSH-9") in segments_by_field
        assert ("PID", "PID-3") in segments_by_field
        assert ("PID", "PID-5") in segments_by_field
        assert ("PV1", "PV1-7") in segments_by_field
        assert ("NK1", "NK1-2") in segments_by_field
        assert ("GT1", "GT1-3") in segments_by_field
        assert ("IN1", "IN1-4") in segments_by_field
        # Citations carry the source_type the supervisor switches on.
        assert all(c.source_type == "hl7_adt" for c in extraction.citations)

    def test_rejects_non_hl7_input(self) -> None:
        with pytest.raises(ValueError, match="not an HL7 message"):
            parse_hl7_adt(b"not an HL7 message", document_id="DocumentReference/doc-x")

    def test_rejects_oru_message(self) -> None:
        # ORU message should not flow through the ADT parser — it has its own.
        with pytest.raises(ValueError, match="not an ADT message"):
            parse_hl7_adt(
                _asset_bytes("p01-chen-oru-r01.hl7"),
                document_id="DocumentReference/doc-x",
            )


# ---------------------------------------------------------------------------
# /upload endpoint
# ---------------------------------------------------------------------------


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
                    "filename": filename,
                    "category": category,
                }
            )
            return True, "doc-adt", None, 1

    class _StubExtractionStore:
        def __init__(self) -> None:
            self.adt_saves: list[dict[str, Any]] = []

        async def save_lab_extraction(self, **kwargs: Any) -> int:
            raise AssertionError("HL7 ADT must not save lab extraction")

        async def save_intake_extraction(self, **kwargs: Any) -> int:
            raise AssertionError("HL7 ADT must not save intake extraction")

        async def save_adt_extraction(self, **kwargs: Any) -> int:
            self.adt_saves.append(kwargs)
            return 1

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


def test_upload_hl7_adt_returns_adt_payload_without_vlm(upload_client: TestClient) -> None:
    response = upload_client.post(
        "/upload",
        files={
            "file": (
                "p01-chen-adt-a08.hl7",
                io.BytesIO(_asset_bytes("p01-chen-adt-a08.hl7")),
                "x-application/hl7-v2+er7",
            ),
        },
        data={
            "patient_id": "patient-chen",
            "doc_type": "hl7_adt",
            "conversation_id": "conv-adt",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["doc_type"] == "hl7_adt"
    assert body["discussable"] is True
    assert body["lab"] is None
    assert body["intake"] is None
    assert body["referral"] is None
    assert body["bboxes"] == []

    adt = body["adt"]
    assert adt is not None, "ADT extraction must populate the adt field"
    assert adt["message_metadata"]["trigger_event"] == "A08"
    assert adt["patient_demographics"]["name"] == "Margaret L Chen"
    assert adt["insurance"][0]["company_name"] == "BLUE SHIELD OF CALIFORNIA PPO"
    citation_segments = {(c["segment"], c["field"]) for c in adt["citations"]}
    assert ("PID", "PID-5") in citation_segments
    assert ("IN1", "IN1-4") in citation_segments

    assert len(upload_client.stub_store.adt_saves) == 1  # type: ignore[attr-defined]
    assert upload_client.stub_store.adt_saves[0]["bboxes"] == []  # type: ignore[attr-defined]
    assert upload_client.system_messages[0].content.startswith(  # type: ignore[attr-defined]
        "[system] Document uploaded:"
    )
    assert "hl7_adt" in upload_client.system_messages[0].content  # type: ignore[attr-defined]

    upload_client.resolve_vlm.assert_not_called()  # type: ignore[attr-defined]
    upload_client.vlm.assert_not_awaited()  # type: ignore[attr-defined]


def test_upload_hl7_adt_with_oru_payload_fails_extraction(
    upload_client: TestClient,
) -> None:
    """An ORU posted as ADT must fail extraction safely (no raw exception)."""
    response = upload_client.post(
        "/upload",
        files={
            "file": (
                "p01-chen-oru-r01.hl7",
                io.BytesIO(_asset_bytes("p01-chen-oru-r01.hl7")),
                "x-application/hl7-v2+er7",
            ),
        },
        data={
            "patient_id": "patient-chen",
            "doc_type": "hl7_adt",
            # confirm to bypass the doc_type_mismatch guard if it fires.
            "confirm_doc_type": "true",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "extraction_failed"
    assert body["adt"] is None
    assert body["discussable"] is False
    assert body["failure_reason"]
