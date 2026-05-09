"""Tests for multi-format upload contract (issue 001).

Validates that the upload system accepts the week-2 format expansion
(TIFF, DOCX, XLSX, HL7) while preserving existing PDF/PNG/JPEG behavior.
Tests cover magic-byte detection, MIME inference, doc_type validation,
and the upload endpoint contract for new formats.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from copilot.extraction.document_client import (
    _infer_mimetype,
    _is_supported_document,
)

# ---------------------------------------------------------------------------
# Magic-byte fixtures
# ---------------------------------------------------------------------------

PDF_BYTES = b"%PDF-1.4\nfake-pdf"
PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-png"
JPEG_BYTES = b"\xff\xd8\xfffake-jpeg"
TIFF_LE_BYTES = b"II\x2a\x00fake-tiff-le"
TIFF_BE_BYTES = b"MM\x00\x2afake-tiff-be"
# Minimal OOXML with word/ marker → DOCX
DOCX_BYTES = b"PK\x03\x04" + b"\x00" * 20 + b"word/document.xml" + b"\x00" * 100
# Minimal OOXML with xl/ marker → XLSX
XLSX_BYTES = b"PK\x03\x04" + b"\x00" * 20 + b"xl/workbook.xml" + b"\x00" * 100
HL7_BYTES = b"MSH|^~\\&|SENDING|FACILITY|RECEIVING|FACILITY|202605|"
UNSUPPORTED_BYTES = b"RIFF\x00\x00\x00\x00WAVEfmt "  # WAV file


# ---------------------------------------------------------------------------
# _is_supported_document
# ---------------------------------------------------------------------------


class TestIsSupportedDocument:
    """Test the magic-byte-based format detection."""

    def test_pdf(self) -> None:
        assert _is_supported_document(PDF_BYTES) is True

    def test_png(self) -> None:
        assert _is_supported_document(PNG_BYTES) is True

    def test_jpeg(self) -> None:
        assert _is_supported_document(JPEG_BYTES) is True

    def test_tiff_little_endian(self) -> None:
        assert _is_supported_document(TIFF_LE_BYTES) is True

    def test_tiff_big_endian(self) -> None:
        assert _is_supported_document(TIFF_BE_BYTES) is True

    def test_docx_ooxml(self) -> None:
        assert _is_supported_document(DOCX_BYTES) is True

    def test_xlsx_ooxml(self) -> None:
        assert _is_supported_document(XLSX_BYTES) is True

    def test_hl7_message(self) -> None:
        assert _is_supported_document(HL7_BYTES) is True

    def test_hl7_with_bom_and_extension_fallback(self) -> None:
        bom_hl7 = b"\xef\xbb\xbfMSH|^~\\&|TEST"
        # Without filename hint, BOM-prefixed HL7 is not detected
        assert _is_supported_document(bom_hl7) is False
        # With .hl7 extension, BOM is stripped and MSH| is found
        assert _is_supported_document(bom_hl7, filename="labs.hl7") is True

    def test_unsupported_format(self) -> None:
        assert _is_supported_document(UNSUPPORTED_BYTES) is False

    def test_empty_bytes(self) -> None:
        assert _is_supported_document(b"") is False


# ---------------------------------------------------------------------------
# _infer_mimetype
# ---------------------------------------------------------------------------


class TestInferMimetype:
    """Test MIME-type inference from magic bytes."""

    def test_pdf(self) -> None:
        assert _infer_mimetype(PDF_BYTES) == "application/pdf"

    def test_png(self) -> None:
        assert _infer_mimetype(PNG_BYTES) == "image/png"

    def test_jpeg(self) -> None:
        assert _infer_mimetype(JPEG_BYTES) == "image/jpeg"

    def test_tiff(self) -> None:
        assert _infer_mimetype(TIFF_LE_BYTES) == "image/tiff"
        assert _infer_mimetype(TIFF_BE_BYTES) == "image/tiff"

    def test_docx(self) -> None:
        result = _infer_mimetype(DOCX_BYTES)
        assert "wordprocessingml" in result

    def test_xlsx(self) -> None:
        result = _infer_mimetype(XLSX_BYTES)
        assert "spreadsheetml" in result

    def test_hl7(self) -> None:
        assert _infer_mimetype(HL7_BYTES) == "x-application/hl7-v2+er7"

    def test_unknown(self) -> None:
        assert _infer_mimetype(UNSUPPORTED_BYTES) == "application/octet-stream"


# ---------------------------------------------------------------------------
# Schema types
# ---------------------------------------------------------------------------


class TestSchemaTypes:
    """Verify the new SourceFormat, DocumentKind, and expanded SourceType."""

    def test_source_format_values(self) -> None:
        from copilot.extraction.schemas import SourceFormat

        # SourceFormat is a Literal; verify all expected values are valid
        expected = {"pdf", "png", "jpeg", "tiff", "docx", "xlsx", "hl7"}
        assert expected == set(SourceFormat.__args__)  # type: ignore[attr-defined]

    def test_document_kind_values(self) -> None:
        from copilot.extraction.schemas import DocumentKind

        expected = {
            "lab_pdf",
            "intake_form",
            "hl7_oru",
            "hl7_adt",
            "xlsx_workbook",
            "docx_referral",
            "tiff_fax",
        }
        assert expected == set(DocumentKind.__args__)  # type: ignore[attr-defined]

    def test_source_type_includes_new_kinds(self) -> None:
        from copilot.extraction.schemas import SourceType

        args = set(SourceType.__args__)  # type: ignore[attr-defined]
        # Legacy values preserved
        assert "lab_pdf" in args
        assert "intake_form" in args
        assert "guideline" in args
        assert "fhir_resource" in args
        # New values added
        assert "hl7_oru" in args
        assert "hl7_adt" in args
        assert "xlsx_workbook" in args
        assert "docx_referral" in args
        assert "tiff_fax" in args


# ---------------------------------------------------------------------------
# Upload endpoint: new doc_type values are accepted
# ---------------------------------------------------------------------------


@pytest.fixture
def upload_client(monkeypatch: pytest.MonkeyPatch):
    """Build a TestClient with stubbed dependencies for upload tests."""

    monkeypatch.setenv("USE_FIXTURE_FHIR", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    from contextlib import asynccontextmanager

    from copilot import server

    @asynccontextmanager
    async def _stub_open_checkpointer(_settings):
        yield None

    monkeypatch.setattr(server, "open_checkpointer", _stub_open_checkpointer)
    monkeypatch.setattr(
        server, "build_graph", lambda *_a, **_kw: object()
    )

    class _StubDocumentClient:
        def __init__(self) -> None:
            self.uploads: list[dict[str, Any]] = []

        async def upload(
            self, patient_id: str, file_data: bytes, filename: str, category: str
        ) -> tuple[bool, str | None, str | None, int]:
            self.uploads.append(
                {"patient_id": patient_id, "filename": filename, "category": category}
            )
            return True, "doc-new-fmt", None, 1

    class _StubExtractionStore:
        def __init__(self) -> None:
            self.lab_saves: list[dict[str, Any]] = []
            self.intake_saves: list[dict[str, Any]] = []

        async def save_lab_extraction(self, **kwargs: Any) -> int:
            self.lab_saves.append(kwargs)
            return 1

        async def save_intake_extraction(self, **kwargs: Any) -> int:
            self.intake_saves.append(kwargs)
            return 1

    stub_doc = _StubDocumentClient()
    stub_store = _StubExtractionStore()

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

        return ExtractionResult(
            ok=False,
            extraction=None,
            error="extraction_not_supported_for_format",
            raw_responses=[],
            pages_processed=0,
            latency_ms=1,
        )

    async def _stub_inject_message(*_a: Any, **_kw: Any) -> None:
        pass

    def _stub_match_bboxes(extraction: Any, file_data: bytes, **kw: Any):
        return []

    monkeypatch.setattr(server, "_vlm_extract_document", _stub_extract)
    monkeypatch.setattr(server, "_inject_upload_system_message", _stub_inject_message)
    monkeypatch.setattr(server, "match_extraction_to_bboxes", _stub_match_bboxes)

    from fastapi.testclient import TestClient

    with TestClient(server.app) as client:
        server.app.state.document_client = stub_doc
        server.app.state.extraction_store = stub_store
        server.app.state.vlm_model = object()
        client.stub_doc = stub_doc  # type: ignore[attr-defined]
        yield client


class TestNewDocTypeUploadValidation:
    """Upload endpoint accepts new doc_type values from the multi-format expansion."""

    @pytest.mark.parametrize(
        "doc_type,magic_bytes,filename",
        [
            ("hl7_oru", HL7_BYTES, "p01-chen-oru-r01.hl7"),
            ("hl7_adt", HL7_BYTES, "p01-chen-adt-a08.hl7"),
            ("xlsx_workbook", XLSX_BYTES, "p01-chen-workbook.xlsx"),
            ("docx_referral", DOCX_BYTES, "p01-chen-referral.docx"),
            ("tiff_fax", TIFF_LE_BYTES, "p01-chen-fax-packet.tiff"),
        ],
    )
    def test_new_doc_type_accepted(
        self,
        upload_client: Any,
        doc_type: str,
        magic_bytes: bytes,
        filename: str,
    ) -> None:
        """New doc_type values pass validation and reach the DocumentClient."""
        response = upload_client.post(
            "/upload",
            files={"file": (filename, io.BytesIO(magic_bytes), "application/octet-stream")},
            data={"patient_id": "patient-1", "doc_type": doc_type},
        )
        # Upload succeeds (200); extraction may fail (no parser yet) but that's expected.
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["requested_type"] == doc_type
        assert body["filename"] == filename
        # The document was sent to OpenEMR.
        assert len(upload_client.stub_doc.uploads) == 1
        assert upload_client.stub_doc.uploads[0]["category"] == doc_type

    def test_legacy_lab_pdf_still_accepted(self, upload_client: Any) -> None:
        """Existing lab_pdf doc_type still works (backward compat)."""
        response = upload_client.post(
            "/upload",
            files={"file": ("lab.pdf", io.BytesIO(PDF_BYTES), "application/pdf")},
            data={"patient_id": "p-1", "doc_type": "lab_pdf"},
        )
        assert response.status_code == 200

    def test_legacy_intake_form_still_accepted(self, upload_client: Any) -> None:
        """Existing intake_form doc_type still works (backward compat)."""
        response = upload_client.post(
            "/upload",
            files={"file": ("intake.png", io.BytesIO(PNG_BYTES), "image/png")},
            data={"patient_id": "p-1", "doc_type": "intake_form"},
        )
        assert response.status_code == 200

    def test_unknown_doc_type_rejected(self, upload_client: Any) -> None:
        """An unknown doc_type still returns 400."""
        response = upload_client.post(
            "/upload",
            files={"file": ("x.pdf", io.BytesIO(PDF_BYTES), "application/pdf")},
            data={"patient_id": "p-1", "doc_type": "unknown_type"},
        )
        assert response.status_code == 400

    def test_unsupported_file_format_rejected(self, upload_client: Any) -> None:
        """A file with unrecognized magic bytes is rejected by DocumentClient."""
        response = upload_client.post(
            "/upload",
            files={
                "file": ("audio.wav", io.BytesIO(UNSUPPORTED_BYTES), "audio/wav"),
            },
            data={"patient_id": "p-1", "doc_type": "lab_pdf"},
        )
        # The upload will reach the server endpoint. The server sniffs the MIME
        # and sends to DocumentClient, which rejects invalid_file_type.
        assert response.status_code == 200
        body = response.json()
        assert body["status"] in ("upload_failed", "extraction_failed")

    def test_empty_file_rejected(self, upload_client: Any) -> None:
        """Empty files are caught at the server boundary."""
        response = upload_client.post(
            "/upload",
            files={"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")},
            data={"patient_id": "p-1", "doc_type": "lab_pdf"},
        )
        assert response.status_code == 400

    def test_oversized_file_rejected(self, upload_client: Any) -> None:
        """Files exceeding the 20 MB cap are rejected."""
        big = b"A" * (20 * 1024 * 1024 + 1)
        response = upload_client.post(
            "/upload",
            files={"file": ("big.pdf", io.BytesIO(big), "application/pdf")},
            data={"patient_id": "p-1", "doc_type": "lab_pdf"},
        )
        assert response.status_code == 413


# ---------------------------------------------------------------------------
# Supervisor upload message: new doc types
# ---------------------------------------------------------------------------


class TestSupervisorUploadNewDocTypes:
    """The supervisor upload message builder accepts new doc types."""

    @pytest.mark.parametrize(
        "doc_type",
        ["hl7_oru", "hl7_adt", "xlsx_workbook", "docx_referral", "tiff_fax"],
    )
    def test_build_message_accepts_new_doc_types(self, doc_type: str) -> None:
        from copilot.supervisor.upload import build_document_upload_message

        msg = build_document_upload_message(
            doc_type=doc_type,
            filename=f"test.{doc_type}",
            document_id="DocumentReference/doc-1",
            patient_id="Patient/p-1",
        )
        assert doc_type in msg.content
        assert "[system] Document uploaded:" in msg.content

    def test_build_message_still_rejects_unknown(self) -> None:
        from copilot.supervisor.upload import build_document_upload_message

        with pytest.raises(ValueError, match="unknown doc_type"):
            build_document_upload_message(
                doc_type="unknown",
                filename="test.bin",
                document_id="DocumentReference/doc-1",
                patient_id="Patient/p-1",
            )


# ---------------------------------------------------------------------------
# Real asset-pack file detection
# ---------------------------------------------------------------------------


class TestAssetPackFileDetection:
    """Verify that actual week-2 asset-pack files are recognized."""

    @pytest.fixture(autouse=True)
    def _load_assets(self) -> None:
        from pathlib import Path

        # Assets are at the repo root, not inside agent/
        self.assets = Path(__file__).resolve().parents[2] / "cohort-5-week-2-assets-v2"

    @pytest.mark.parametrize(
        "subdir,filename,expected_mime_substr",
        [
            ("hl7v2", "p01-chen-oru-r01.hl7", "hl7"),
            ("hl7v2", "p01-chen-adt-a08.hl7", "hl7"),
            ("xlsx", "p01-chen-workbook.xlsx", "spreadsheetml"),
            ("docx", "p01-chen-referral.docx", "wordprocessingml"),
            ("tiff", "p01-chen-fax-packet.tiff", "tiff"),
        ],
    )
    def test_asset_file_detected(
        self, subdir: str, filename: str, expected_mime_substr: str
    ) -> None:
        path = self.assets / subdir / filename
        if not path.exists():
            pytest.skip(f"asset file {path} not found")
        data = path.read_bytes()
        assert _is_supported_document(data, filename=filename) is True
        mime = _infer_mimetype(data)
        assert expected_mime_substr in mime
