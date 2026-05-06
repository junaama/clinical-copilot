"""Unit tests for ``copilot.extraction.vlm``.

The VLM call is mocked through a stub model that mimics the
``with_structured_output(..., include_raw=True)`` envelope LangChain returns.
That keeps the tests fast and offline — Anthropic is never reached.

Coverage:

- Mimetype handling (PDF → multi-page, PNG/JPEG → single page, unknown → fail).
- ``extract_lab`` happy path on a single-page image.
- Multi-page lab merge: top-level fields preferred from first non-None page,
  ``results`` concatenated.
- ``extract_intake`` happy path with all required fields.
- Validation error path: every page fails to parse → ``ok=False`` with raw
  responses preserved for debug.
- Mixed success: one page parses, another fails → merged result still ok.
- ``extract_document`` dispatch by ``doc_type``.
- ``build_vision_model`` raises when ``ANTHROPIC_API_KEY`` is unset.
- A real fixture PDF renders the expected number of page images.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from copilot.config import Settings
from copilot.extraction.schemas import (
    IntakeAllergy,
    IntakeDemographics,
    IntakeExtraction,
    IntakeMedication,
    LabExtraction,
    LabResult,
    SourceCitation,
)
from copilot.extraction.vlm import (
    ExtractionResult,
    _render_pages,
    extract_document,
    extract_intake,
    extract_lab,
)
from copilot.llm import build_vision_model

# Smallest valid PNG: 1x1 pixel, transparent. We never actually open it as
# an image in tests — the stub model bypasses the real VLM — so the bytes
# only need to satisfy ``_render_pages`` and the callers' size sanity checks.
_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452"
    "00000001000000010806000000"
    "1f15c4890000000d49444154789c63"
    "f8ffff3f000005fe02fe98a4d4b50000"
    "000049454e44ae426082"
)

_TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300010101"
    "010101010101010101010101010101010101010101010101010101"
    "01010101010101010101010101010101010101010101010101010101"
    "0101010101010101010101010101ffc00011080001000101011100"
    "ffc4001f0000010501010101010100000000000000000102030405"
    "060708090a0bffc400b5100002010303020403050504040000017d01"
    "020300041105122131410613516107227114328191a1082342b1c11552"
    "d1f02433627282090a161718191a25262728292a3435363738393a4344"
    "45464748494a535455565758595a636465666768696a737475767778"
    "797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2"
    "b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3"
    "e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fb00"
    "ffd9"
)

_FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / "example-documents"
_FIXTURE_LAB_PDF = _FIXTURE_DIR / "lab-results" / "p01-chen-lipid-panel.pdf"


# ---------------------------------------------------------------------------
# Stub model
# ---------------------------------------------------------------------------


class _StubReply:
    """Mimics LangChain's ``include_raw=True`` envelope."""

    def __init__(self, *, parsed: Any = None, raw_text: str = "", error: Exception | None = None):
        self._parsed = parsed
        self._raw_text = raw_text
        self._error = error

    def as_dict(self) -> dict[str, Any]:
        class _Raw:
            def __init__(self, content: str) -> None:
                self.content = content

        return {
            "raw": _Raw(self._raw_text),
            "parsed": self._parsed,
            "parsing_error": self._error,
        }


class _StubVisionModel:
    """Hand-rolls the surface ``vlm.py`` uses on a chat model.

    ``with_structured_output`` returns self so subsequent ``ainvoke`` calls
    consume the queued ``_StubReply`` envelopes one per page.
    """

    def __init__(self, replies: list[_StubReply | Exception]) -> None:
        self._queue: list[_StubReply | Exception] = list(replies)
        self.invocations: list[Any] = []
        self.bind_called_with: tuple[Any, dict[str, Any]] | None = None

    def with_structured_output(self, schema: Any, *, include_raw: bool = False) -> _StubVisionModel:
        self.bind_called_with = (schema, {"include_raw": include_raw})
        return self

    async def ainvoke(self, messages: Any, **_kwargs: Any) -> Any:
        self.invocations.append(messages)
        if not self._queue:
            raise RuntimeError("StubVisionModel exhausted")
        next_reply = self._queue.pop(0)
        if isinstance(next_reply, Exception):
            raise next_reply
        return next_reply.as_dict()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lab_citation() -> SourceCitation:
    return SourceCitation(
        source_type="lab_pdf",
        source_id="DocumentReference/doc-stub",
        page_or_section="page 1",
        field_or_chunk_id="results[0].value",
        quote_or_value="180",
    )


def _intake_citation() -> SourceCitation:
    return SourceCitation(
        source_type="intake_form",
        source_id="DocumentReference/doc-stub",
        page_or_section="page 1",
    )


def _make_lab_extraction(
    *,
    patient_name: str | None = "Eduardo Chen",
    results: list[LabResult] | None = None,
) -> LabExtraction:
    if results is None:
        results = [
            LabResult(
                test_name="Total Cholesterol",
                value="180",
                unit="mg/dL",
                reference_range="<200",
                collection_date="2026-04-12",
                abnormal_flag="normal",
                confidence="high",
                source_citation=_lab_citation(),
            )
        ]
    return LabExtraction(
        patient_name=patient_name,
        collection_date="2026-04-12",
        ordering_provider="Dr. Patel",
        lab_name="Quest",
        results=results,
        source_document_id="DocumentReference/doc-stub",
        extraction_model="stub",
        extraction_timestamp="2026-04-12T10:00:00+00:00",
    )


def _make_intake_extraction() -> IntakeExtraction:
    return IntakeExtraction(
        demographics=IntakeDemographics(
            name="Eduardo Chen",
            dob="1972-03-14",
            gender="M",
            phone="555-0100",
        ),
        chief_concern="Annual physical and follow-up on hypertension.",
        current_medications=[
            IntakeMedication(name="Lisinopril", dose="10mg", frequency="daily"),
        ],
        allergies=[
            IntakeAllergy(substance="penicillin", reaction="rash", severity="mild"),
        ],
        family_history=[],
        social_history=None,
        source_citation=_intake_citation(),
        source_document_id="DocumentReference/doc-stub",
        extraction_model="stub",
        extraction_timestamp="2026-04-12T10:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# _render_pages
# ---------------------------------------------------------------------------


def test_render_passthrough_png() -> None:
    pages = _render_pages(_TINY_PNG, "image/png")
    assert pages == [_TINY_PNG]


def test_render_passthrough_jpeg_normalizes_mimetype() -> None:
    pages = _render_pages(_TINY_JPEG, "image/jpg")
    assert pages == [_TINY_JPEG]


def test_render_unsupported_mimetype_raises() -> None:
    from copilot.extraction.vlm import _RenderError

    with pytest.raises(_RenderError):
        _render_pages(b"not-a-pdf", "application/octet-stream")


@pytest.mark.skipif(not _FIXTURE_LAB_PDF.exists(), reason="fixture PDF missing")
def test_render_real_pdf_produces_per_page_pngs() -> None:
    file_data = _FIXTURE_LAB_PDF.read_bytes()
    pages = _render_pages(file_data, "application/pdf")
    assert len(pages) >= 1
    # PNG magic header — confirms we got an actual PNG out of PyMuPDF.
    assert all(p.startswith(b"\x89PNG\r\n\x1a\n") for p in pages)


# ---------------------------------------------------------------------------
# extract_lab — happy paths
# ---------------------------------------------------------------------------


def test_extract_lab_single_page_image_success() -> None:
    expected = _make_lab_extraction()
    stub = _StubVisionModel([_StubReply(parsed=expected, raw_text="{...}")])

    result = asyncio.run(
        extract_lab(
            _TINY_PNG,
            mimetype="image/png",
            document_id="DocumentReference/doc-42",
            model=stub,  # type: ignore[arg-type]
        )
    )

    assert isinstance(result, ExtractionResult)
    assert result.ok is True
    assert result.error is None
    assert result.pages_processed == 1
    assert isinstance(result.extraction, LabExtraction)
    assert result.extraction is not None
    assert len(result.extraction.results) == 1
    assert result.extraction.source_document_id == "DocumentReference/doc-42"
    # Caller's document_id wins over whatever the VLM returned. Lab citations
    # live on each LabResult, not at the top level.
    assert result.extraction.results[0].source_citation.source_id == "DocumentReference/doc-42"
    assert result.extraction.results[0].source_citation.source_type == "lab_pdf"

    # Stub bound the lab schema with include_raw=True.
    assert stub.bind_called_with is not None
    schema, kwargs = stub.bind_called_with
    assert schema is LabExtraction
    assert kwargs == {"include_raw": True}


def test_extract_lab_multi_page_merges_results() -> None:
    page_one = _make_lab_extraction(
        patient_name="Eduardo Chen",
        results=[
            LabResult(
                test_name="HDL",
                value="55",
                unit="mg/dL",
                reference_range=">40",
                collection_date="2026-04-12",
                abnormal_flag="normal",
                confidence="high",
                source_citation=_lab_citation(),
            )
        ],
    )
    page_two = _make_lab_extraction(
        patient_name=None,  # second page often has no header
        results=[
            LabResult(
                test_name="LDL",
                value="142",
                unit="mg/dL",
                reference_range="<100",
                collection_date="2026-04-12",
                abnormal_flag="high",
                confidence="high",
                source_citation=_lab_citation(),
            ),
            LabResult(
                test_name="Triglycerides",
                value="180",
                unit="mg/dL",
                reference_range="<150",
                collection_date="2026-04-12",
                abnormal_flag="high",
                confidence="medium",
                source_citation=_lab_citation(),
            ),
        ],
    )
    stub = _StubVisionModel([_StubReply(parsed=page_one), _StubReply(parsed=page_two)])

    # Use the real lipid-panel PDF so we get a 2-page render.
    if not _FIXTURE_LAB_PDF.exists():
        pytest.skip("fixture PDF missing")
    file_data = _FIXTURE_LAB_PDF.read_bytes()

    result = asyncio.run(
        extract_lab(
            file_data,
            mimetype="application/pdf",
            document_id="DocumentReference/doc-99",
            model=stub,  # type: ignore[arg-type]
        )
    )

    assert result.ok is True
    assert result.pages_processed >= 2
    assert result.extraction is not None
    assert isinstance(result.extraction, LabExtraction)
    test_names = [r.test_name for r in result.extraction.results]
    assert "HDL" in test_names
    assert "LDL" in test_names
    assert "Triglycerides" in test_names
    # Top-level identifier carried forward from page 1.
    assert result.extraction.patient_name == "Eduardo Chen"


def test_extract_lab_low_confidence_preserved() -> None:
    expected = _make_lab_extraction(
        results=[
            LabResult(
                test_name="HDL",
                value="55",
                unit="mg/dL",
                reference_range=">40",
                collection_date="2026-04-12",
                abnormal_flag="normal",
                confidence="low",
                source_citation=_lab_citation(),
            )
        ]
    )
    stub = _StubVisionModel([_StubReply(parsed=expected)])

    result = asyncio.run(
        extract_lab(
            _TINY_PNG,
            mimetype="image/png",
            document_id="DocumentReference/doc-1",
            model=stub,  # type: ignore[arg-type]
        )
    )
    assert result.ok is True
    assert result.extraction is not None
    assert isinstance(result.extraction, LabExtraction)
    assert result.extraction.results[0].confidence == "low"


# ---------------------------------------------------------------------------
# extract_intake
# ---------------------------------------------------------------------------


def test_extract_intake_single_page_image_success() -> None:
    expected = _make_intake_extraction()
    stub = _StubVisionModel([_StubReply(parsed=expected)])

    result = asyncio.run(
        extract_intake(
            _TINY_PNG,
            mimetype="image/png",
            document_id="DocumentReference/intake-1",
            model=stub,  # type: ignore[arg-type]
        )
    )

    assert result.ok is True
    assert result.extraction is not None
    assert isinstance(result.extraction, IntakeExtraction)
    assert result.extraction.chief_concern.startswith("Annual physical")
    assert len(result.extraction.current_medications) == 1
    assert result.extraction.current_medications[0].name == "Lisinopril"
    assert result.extraction.allergies[0].substance == "penicillin"
    assert result.extraction.source_document_id == "DocumentReference/intake-1"


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------


def test_extract_lab_unsupported_mimetype_returns_typed_error() -> None:
    stub = _StubVisionModel([])
    result = asyncio.run(
        extract_lab(
            b"random",
            mimetype="text/plain",
            document_id="DocumentReference/doc-1",
            model=stub,  # type: ignore[arg-type]
        )
    )
    assert result.ok is False
    assert result.error is not None
    assert "unsupported mimetype" in result.error
    assert result.extraction is None
    assert result.pages_processed == 0


def test_extract_lab_all_pages_fail_to_parse() -> None:
    err = ValueError("schema validation failed")
    stub = _StubVisionModel(
        [_StubReply(parsed=None, raw_text="not-json", error=err)]
    )
    result = asyncio.run(
        extract_lab(
            _TINY_PNG,
            mimetype="image/png",
            document_id="DocumentReference/doc-1",
            model=stub,  # type: ignore[arg-type]
        )
    )
    assert result.ok is False
    assert result.extraction is None
    assert result.error is not None
    assert "schema validation failed" in result.error
    # Raw response preserved so callers can debug.
    assert "not-json" in result.raw_responses[0]


def test_extract_lab_partial_failure_still_succeeds() -> None:
    err = ValueError("page 1 unparseable")
    good_page = _make_lab_extraction()
    stub = _StubVisionModel(
        [
            _StubReply(parsed=None, raw_text="garbled", error=err),
            _StubReply(parsed=good_page),
        ]
    )

    if not _FIXTURE_LAB_PDF.exists():
        pytest.skip("fixture PDF missing")
    file_data = _FIXTURE_LAB_PDF.read_bytes()

    result = asyncio.run(
        extract_lab(
            file_data,
            mimetype="application/pdf",
            document_id="DocumentReference/doc-x",
            model=stub,  # type: ignore[arg-type]
        )
    )
    assert result.ok is True
    assert result.extraction is not None
    assert isinstance(result.extraction, LabExtraction)
    assert result.extraction.results, "expected merged extraction to carry second-page results"


# ---------------------------------------------------------------------------
# extract_document dispatch
# ---------------------------------------------------------------------------


def test_extract_document_dispatches_lab() -> None:
    expected = _make_lab_extraction()
    stub = _StubVisionModel([_StubReply(parsed=expected)])
    result = asyncio.run(
        extract_document(
            _TINY_PNG,
            mimetype="image/png",
            doc_type="lab_pdf",
            document_id="DocumentReference/d1",
            model=stub,  # type: ignore[arg-type]
        )
    )
    assert result.ok is True
    assert isinstance(result.extraction, LabExtraction)


def test_extract_document_dispatches_intake() -> None:
    expected = _make_intake_extraction()
    stub = _StubVisionModel([_StubReply(parsed=expected)])
    result = asyncio.run(
        extract_document(
            _TINY_PNG,
            mimetype="image/png",
            doc_type="intake_form",
            document_id="DocumentReference/d1",
            model=stub,  # type: ignore[arg-type]
        )
    )
    assert result.ok is True
    assert isinstance(result.extraction, IntakeExtraction)


# ---------------------------------------------------------------------------
# build_vision_model
# ---------------------------------------------------------------------------


def test_build_vision_model_requires_anthropic_key() -> None:
    settings = Settings(
        ANTHROPIC_API_KEY=SecretStr(""),
        OPENAI_API_KEY=SecretStr(""),
        SESSION_SECRET=SecretStr("test"),
    )
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_vision_model(settings)


def test_build_vision_model_returns_chat_model_when_key_present() -> None:
    settings = Settings(
        ANTHROPIC_API_KEY=SecretStr("sk-ant-test"),
        OPENAI_API_KEY=SecretStr(""),
        SESSION_SECRET=SecretStr("test"),
    )
    model = build_vision_model(settings)
    # We don't assert the concrete type (langchain_anthropic isn't imported
    # at the test-module level) — just that we got back something with the
    # ``invoke``/``ainvoke`` shape every chat model exposes.
    assert hasattr(model, "ainvoke")
    assert hasattr(model, "with_structured_output")
