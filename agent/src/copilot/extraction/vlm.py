"""VLM extraction pipeline.

Converts a PDF or image (PNG/JPEG/TIFF) into a validated ``LabExtraction`` or
``IntakeExtraction`` payload by:

1. Rendering the document to per-page PNG bytes (PyMuPDF for PDFs, raw
   passthrough for single-page PNG/JPEG images, PyMuPDF for multipage TIFFs).
2. Calling Claude Sonnet 4 with a structured-output prompt that targets the
   appropriate Pydantic schema. Confidence (high/medium/low) is part of the
   prompt instruction so values that are partially obscured / handwritten /
   ambiguous are surfaced rather than asserted.
3. Validating each page's response against the schema and merging multi-page
   results into a single extraction.

Design decisions:

* Sequential per-page calls (not fan-out). Multi-page lab PDFs are uncommon
  in the demo corpus and concurrent vision calls don't reliably interleave
  with structured-output retries; sequential keeps the merge simple.
* ``with_structured_output(..., include_raw=True)`` so a malformed VLM
  response surfaces as ``ExtractionResult.error`` with the raw text attached
  for debugging, instead of bubbling a ``ValidationError`` up to the caller.
* The caller injects the model. Tests pass a stub that mimics
  ``ainvoke``-on-structured-output; production callers pass
  ``build_vision_model(settings)`` from ``copilot.llm``.
"""

from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from copilot.extraction.schemas import (
    IntakeExtraction,
    LabExtraction,
)

_log = logging.getLogger(__name__)

DocType = Literal["lab_pdf", "intake_form", "tiff_fax"]

# Mimetypes the VLM pipeline accepts. Anything else short-circuits with a
# typed error before we render.
_PDF_MIMETYPES = frozenset({"application/pdf"})
_IMAGE_MIMETYPES: dict[str, str] = {
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
}
_TIFF_MIMETYPES = frozenset({"image/tiff", "image/tif"})

# DPI for PDF page rendering. 200 is a good balance: high enough that
# typed lab values are crisp, low enough that the resulting PNG fits inside
# Anthropic's per-image upload limit comfortably.
_RENDER_DPI = 200
_TIFF_RENDER_DPI = 150


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of one ``extract_*`` call.

    ``ok=True`` iff at least one page parsed cleanly into the target schema
    and the merged extraction validates. ``raw_responses`` carries one entry
    per page so debugging a single-page failure inside a multi-page extract
    doesn't require re-running the call.
    """

    ok: bool
    extraction: LabExtraction | IntakeExtraction | None
    error: str | None
    raw_responses: list[str] = field(default_factory=list)
    pages_processed: int = 0
    latency_ms: int = 0


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

# Trimmed JSON schemas shown to the VLM. We don't pass the Pydantic-generated
# schema verbatim because it is verbose and includes pydantic-internal
# ``$defs`` references that confuse the model. Instead, hand-write a compact
# shape description that mirrors the schemas in
# ``copilot.extraction.schemas`` and the W2 architecture doc.

_LAB_SYSTEM_PROMPT = """\
You extract clinical lab results from a single page of a lab report.
Return a JSON object that matches the LabExtraction schema. Confidence:

  high   = value is clearly typed/printed and unambiguous
  medium = legible but partially obscured / non-standard format
  low    = handwritten, faint, or you're guessing — flag rather than omit

Rules:
  * test_name, value, unit, abnormal_flag, confidence are required for every result row.
  * abnormal_flag must be one of: high, low, critical_high, critical_low, normal, unknown.
  * If the document marks a value abnormal (H, L, *), set abnormal_flag accordingly.
  * If reference range is shown, populate reference_range; otherwise null.
  * Quote the literal value as it appears (preserve units, decimal places).
  * source_citation.source_type must be "lab_pdf" and source_id will be filled by the caller.
  * Never invent values. If a row is unreadable, omit it from results.
  * For each result row, provide vlm_bbox with the bounding box of that
    result row on the page in normalized coordinates:
    {"page": <page_number>, "bbox": [x0, y0, x1, y1]}
    where each coordinate is in [0, 1] range (0,0 = top-left corner of
    the page, 1,1 = bottom-right corner). The bbox should tightly enclose
    the entire result row (test name through unit/reference range).
    If you cannot determine the bounding box, set vlm_bbox to null.
"""

_TIFF_FAX_SYSTEM_PROMPT = """\
You extract clinical lab results from one page of a multipage fax packet.
The packet may include a cover sheet, referral request, face sheet, and
lab report pages. Return a JSON object that matches the LabExtraction
schema for THIS PAGE ONLY.

Confidence:

  high   = value is clearly typed/printed and unambiguous
  medium = legible but partially obscured / fax artifacts affect readability
  low    = faint, noisy, skewed, handwritten, or you're guessing — flag rather than omit

Rules:
  * Extract lab result rows only; if this page is not a lab report page,
    return an empty results list and any clearly readable top-level patient
    identifiers.
  * test_name, value, unit, abnormal_flag, confidence are required for every result row.
  * abnormal_flag must be one of: high, low, critical_high, critical_low, normal, unknown.
  * If the document marks a value abnormal (H, L, *), set abnormal_flag accordingly.
  * If reference range is shown, populate reference_range; otherwise null.
  * Quote the literal value as it appears (preserve units, decimal places).
  * source_citation.source_type must be "tiff_fax" and source_id will be filled by the caller.
  * source_citation.page_or_section must identify the current page, e.g. "page 4".
  * Never invent values. If a row is unreadable, omit it from results.
  * Use confidence="low" for clinically important values affected by fax
    noise or ambiguous glyphs; do not assert uncertain scans as high confidence.
  * For each result row, provide vlm_bbox with the bounding box of that
    result row on the page in normalized coordinates:
    {"page": <page_number>, "bbox": [x0, y0, x1, y1]}
    where each coordinate is in [0, 1] range (0,0 = top-left corner of
    the page, 1,1 = bottom-right corner). The bbox should tightly enclose
    the entire result row (test name through unit/reference range).
    If you cannot determine the bounding box, set vlm_bbox to null.
"""

_INTAKE_SYSTEM_PROMPT = """\
You extract structured fields from a single page of a patient intake form.
Return a JSON object that matches the IntakeExtraction schema.

Rules:
  * demographics may be partially populated — leave unknown fields null.
  * chief_concern is required (the reason the patient is visiting).
  * current_medications, allergies, family_history are required keys; the
    lists may be empty if the patient indicates none.
  * For each medication: name is required; dose/frequency/prescriber may be null.
  * For each allergy: substance is required; reaction/severity may be null.
  * social_history is fully optional — leave entirely null if the form has
    no social-history section.
  * source_citation.source_type must be "intake_form" and source_id will be
    filled by the caller.
  * Never invent values. Leave a field null if you cannot read it.
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def extract_document(
    file_data: bytes,
    mimetype: str,
    doc_type: DocType,
    *,
    document_id: str,
    model: BaseChatModel,
    extraction_model_name: str = "claude-sonnet-4-6",
) -> ExtractionResult:
    """Extract structured data from a PDF or image.

    Dispatches to ``extract_lab`` or ``extract_intake`` based on ``doc_type``.
    Returns an ``ExtractionResult`` with either a typed extraction or an
    error message plus the raw VLM responses for debugging.
    """

    if doc_type == "lab_pdf":
        return await extract_lab(
            file_data,
            mimetype,
            document_id=document_id,
            model=model,
            extraction_model_name=extraction_model_name,
        )
    if doc_type == "intake_form":
        return await extract_intake(
            file_data,
            mimetype,
            document_id=document_id,
            model=model,
            extraction_model_name=extraction_model_name,
        )
    if doc_type == "tiff_fax":
        return await extract_tiff_fax(
            file_data,
            mimetype,
            document_id=document_id,
            model=model,
            extraction_model_name=extraction_model_name,
        )
    return _failure(
        error=f"unknown doc_type: {doc_type!r}",
        raw_responses=[],
        pages_processed=0,
        latency_ms=0,
    )


async def extract_lab(
    file_data: bytes,
    mimetype: str,
    *,
    document_id: str,
    model: BaseChatModel,
    extraction_model_name: str = "claude-sonnet-4-6",
) -> ExtractionResult:
    """Extract a ``LabExtraction`` from a lab PDF or single-page image."""

    return await _extract(
        file_data=file_data,
        mimetype=mimetype,
        document_id=document_id,
        model=model,
        target_schema=LabExtraction,
        system_prompt=_LAB_SYSTEM_PROMPT,
        source_type="lab_pdf",
        extraction_model_name=extraction_model_name,
    )


async def extract_intake(
    file_data: bytes,
    mimetype: str,
    *,
    document_id: str,
    model: BaseChatModel,
    extraction_model_name: str = "claude-sonnet-4-6",
) -> ExtractionResult:
    """Extract an ``IntakeExtraction`` from an intake-form PDF or image."""

    return await _extract(
        file_data=file_data,
        mimetype=mimetype,
        document_id=document_id,
        model=model,
        target_schema=IntakeExtraction,
        system_prompt=_INTAKE_SYSTEM_PROMPT,
        source_type="intake_form",
        extraction_model_name=extraction_model_name,
    )


async def extract_tiff_fax(
    file_data: bytes,
    mimetype: str,
    *,
    document_id: str,
    model: BaseChatModel,
    extraction_model_name: str = "claude-sonnet-4-6",
) -> ExtractionResult:
    """Extract lab rows from a multipage TIFF fax packet."""

    return await _extract(
        file_data=file_data,
        mimetype=mimetype,
        document_id=document_id,
        model=model,
        target_schema=LabExtraction,
        system_prompt=_TIFF_FAX_SYSTEM_PROMPT,
        source_type="tiff_fax",
        extraction_model_name=extraction_model_name,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


async def _extract(
    *,
    file_data: bytes,
    mimetype: str,
    document_id: str,
    model: BaseChatModel,
    target_schema: type[BaseModel],
    system_prompt: str,
    source_type: str,
    extraction_model_name: str,
) -> ExtractionResult:
    started = time.perf_counter()

    try:
        pages = _render_pages(file_data, mimetype)
    except _RenderError as exc:
        return _failure(
            error=str(exc),
            raw_responses=[],
            pages_processed=0,
            latency_ms=_elapsed_ms(started),
        )

    if not pages:
        return _failure(
            error="document produced zero pages",
            raw_responses=[],
            pages_processed=0,
            latency_ms=_elapsed_ms(started),
        )

    structured_model = model.with_structured_output(target_schema, include_raw=True)
    user_prompt = _user_prompt(document_id, source_type)

    raw_responses: list[str] = []
    parsed_pages: list[BaseModel] = []
    last_error: str | None = None

    for page_index, page_bytes in enumerate(pages, start=1):
        messages = _build_messages(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            page_index=page_index,
            page_bytes=page_bytes,
            page_mimetype="image/png",
        )
        try:
            reply = await structured_model.ainvoke(messages)
        except Exception as exc:  # pragma: no cover — network / SDK failures
            _log.warning("vlm page=%d invoke failed: %s", page_index, exc)
            raw_responses.append("")
            last_error = f"page {page_index}: {type(exc).__name__}: {exc}"
            continue

        raw_text, parsed, parse_error = _unpack_structured_reply(reply)
        raw_responses.append(raw_text)
        if parsed is None:
            last_error = f"page {page_index}: {parse_error or 'no parsed extraction'}"
            continue
        parsed_pages.append(parsed)

    if not parsed_pages:
        return _failure(
            error=last_error or "all pages failed to parse",
            raw_responses=raw_responses,
            pages_processed=len(pages),
            latency_ms=_elapsed_ms(started),
        )

    try:
        merged = _merge_pages(
            parsed_pages,
            target_schema=target_schema,
            document_id=document_id,
            source_type=source_type,
            extraction_model_name=extraction_model_name,
        )
    except ValidationError as exc:
        return _failure(
            error=f"merge failed schema validation: {exc.errors()[:3]}",
            raw_responses=raw_responses,
            pages_processed=len(pages),
            latency_ms=_elapsed_ms(started),
        )

    return ExtractionResult(
        ok=True,
        extraction=merged,
        error=None,
        raw_responses=raw_responses,
        pages_processed=len(pages),
        latency_ms=_elapsed_ms(started),
    )


def _failure(
    *,
    error: str,
    raw_responses: list[str],
    pages_processed: int,
    latency_ms: int,
) -> ExtractionResult:
    return ExtractionResult(
        ok=False,
        extraction=None,
        error=error,
        raw_responses=raw_responses,
        pages_processed=pages_processed,
        latency_ms=latency_ms,
    )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------


class _RenderError(RuntimeError):
    """Raised by ``_render_pages`` when input cannot be rendered."""


def _render_pages(file_data: bytes, mimetype: str) -> list[bytes]:
    """Return one PNG-encoded byte string per page."""

    normalized = (mimetype or "").lower().strip()
    if normalized in _PDF_MIMETYPES:
        return _render_pdf(file_data)
    if normalized in _IMAGE_MIMETYPES:
        # Single-page image — return as-is. The Anthropic API accepts PNG and
        # JPEG natively; we don't need to re-encode.
        return [file_data]
    if normalized in _TIFF_MIMETYPES:
        return _render_tiff(file_data)
    raise _RenderError(f"unsupported mimetype {mimetype!r}")


def _render_pdf(file_data: bytes) -> list[bytes]:
    try:
        import pymupdf  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover — dev install issue only
        raise _RenderError(f"pymupdf not installed: {exc}") from exc

    try:
        doc = pymupdf.open(stream=file_data, filetype="pdf")
    except Exception as exc:
        raise _RenderError(f"PDF parse failed: {exc}") from exc

    pages: list[bytes] = []
    try:
        for page in doc:
            pix = page.get_pixmap(dpi=_RENDER_DPI)
            pages.append(pix.tobytes("png"))
    finally:
        doc.close()
    return pages


def _render_tiff(file_data: bytes) -> list[bytes]:
    try:
        import pymupdf  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover — dev install issue only
        raise _RenderError(f"pymupdf not installed: {exc}") from exc

    try:
        doc = pymupdf.open(stream=file_data, filetype="tiff")
    except Exception as exc:
        raise _RenderError(f"TIFF parse failed: {exc}") from exc

    pages: list[bytes] = []
    try:
        for page in doc:
            pix = page.get_pixmap(dpi=_TIFF_RENDER_DPI)
            pages.append(pix.tobytes("png"))
    finally:
        doc.close()
    return pages


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _user_prompt(document_id: str, source_type: str) -> str:
    return (
        "Extract structured data from the page image attached. "
        f"Set source_document_id to {document_id!r} and "
        f"source_citation.source_id to {document_id!r}, "
        f"source_citation.source_type to {source_type!r}. "
        "Respond ONLY with JSON matching the schema."
    )


def _build_messages(
    *,
    system_prompt: str,
    user_prompt: str,
    page_index: int,
    page_bytes: bytes,
    page_mimetype: str,
) -> list[Any]:
    page_marker = f"This is page {page_index}. {user_prompt}"
    encoded = base64.b64encode(page_bytes).decode("ascii")
    return [
        SystemMessage(content=system_prompt),
        HumanMessage(
            content=[
                {"type": "text", "text": page_marker},
                {
                    "type": "image",
                    "source_type": "base64",
                    "mime_type": page_mimetype,
                    "data": encoded,
                },
            ]
        ),
    ]


def _unpack_structured_reply(reply: Any) -> tuple[str, BaseModel | None, str | None]:
    """Pull (raw_text, parsed_model, error_str) out of a structured-output reply.

    LangChain's ``with_structured_output(..., include_raw=True)`` returns a
    dict ``{"raw": AIMessage, "parsed": Model | None, "parsing_error": Exception | None}``.
    We tolerate alternate shapes (e.g. a stub that returns the parsed model
    directly) so tests don't have to mimic the dict envelope.
    """

    if isinstance(reply, BaseModel):
        return ("", reply, None)
    if isinstance(reply, dict):
        raw = reply.get("raw")
        raw_text = ""
        if raw is not None:
            content = getattr(raw, "content", raw)
            raw_text = content if isinstance(content, str) else str(content)
        parsed = reply.get("parsed")
        parsing_error = reply.get("parsing_error")
        error_str = (
            f"{type(parsing_error).__name__}: {parsing_error}"
            if parsing_error is not None
            else None
        )
        if parsed is not None and not isinstance(parsed, BaseModel):
            return (raw_text, None, f"parsed value is not a Pydantic model: {type(parsed)}")
        return (raw_text, parsed, error_str)
    return ("", None, f"unexpected reply shape: {type(reply).__name__}")


# ---------------------------------------------------------------------------
# Multi-page merging
# ---------------------------------------------------------------------------


def _merge_pages(
    parsed_pages: list[BaseModel],
    *,
    target_schema: type[BaseModel],
    document_id: str,
    source_type: str,
    extraction_model_name: str,
) -> BaseModel:
    """Combine per-page extractions into a single validated payload.

    For lab extractions: concatenate ``results`` across pages, prefer the
    first non-None top-level identifier (patient_name, collection_date, …).
    For intake extractions: similar — concatenate the list fields, prefer
    first non-None for non-list fields.

    The merged object is re-validated against ``target_schema`` so any
    malformed concatenation raises ``ValidationError`` instead of silently
    producing a half-typed result.
    """

    timestamp = datetime.now(UTC).isoformat()

    if target_schema is LabExtraction:
        merged_data = _merge_lab(
            parsed_pages,
            stamp_page_citations=source_type == "tiff_fax",
        )
        _restamp_lab_result_citations(
            merged_data["results"],
            document_id=document_id,
            source_type=source_type,
        )
    elif target_schema is IntakeExtraction:
        merged_data = _merge_intake(parsed_pages)
    else:  # pragma: no cover — only the two known schemas are wired in
        raise RuntimeError(f"unsupported merge target schema: {target_schema!r}")

    # Always re-stamp authoritative metadata. The VLM is told to populate
    # ``source_document_id`` but we do not trust it to be exactly right
    # (model can hallucinate the id format), so the caller's value wins.
    merged_data["source_document_id"] = document_id
    merged_data["extraction_model"] = extraction_model_name
    merged_data["extraction_timestamp"] = timestamp

    # ``source_citation`` is a top-level field on IntakeExtraction but not on
    # LabExtraction (lab citations live per-LabResult). Only attach it where
    # the schema accepts it, otherwise ``extra='forbid'`` rejects validation.
    if "source_citation" in target_schema.model_fields:
        citation = merged_data.get("source_citation")
        if not isinstance(citation, dict):
            citation = {
                "source_type": source_type,
                "source_id": document_id,
            }
        else:
            citation = dict(citation)
            citation.setdefault("source_type", source_type)
            citation["source_id"] = document_id
        merged_data["source_citation"] = citation
    else:
        merged_data.pop("source_citation", None)

    return target_schema.model_validate(merged_data)


def _merge_lab(
    parsed_pages: list[BaseModel],
    *,
    stamp_page_citations: bool = False,
) -> dict[str, Any]:
    """Concatenate ``results``, prefer first non-None top-level fields."""

    merged: dict[str, Any] = {
        "patient_name": None,
        "collection_date": None,
        "ordering_provider": None,
        "lab_name": None,
        "results": [],
    }
    for page_index, page in enumerate(parsed_pages, start=1):
        page_dict = page.model_dump()
        for key in ("patient_name", "collection_date", "ordering_provider", "lab_name"):
            if merged[key] is None and page_dict.get(key) is not None:
                merged[key] = page_dict[key]
        page_results = page_dict.get("results") or []
        if stamp_page_citations:
            for result in page_results:
                citation = result.get("source_citation")
                if isinstance(citation, dict):
                    citation = dict(citation)
                    citation["page_or_section"] = f"page {page_index}"
                    result["source_citation"] = citation
        merged["results"].extend(page_results)
    return merged


def _restamp_lab_result_citations(
    results: list[dict[str, Any]],
    *,
    document_id: str,
    source_type: str,
) -> None:
    """Overwrite each ``LabResult.source_citation.source_id`` with the
    caller's document_id. The VLM is instructed to populate the citation
    but we don't trust it to format the id correctly — the caller wins.

    Mutates in place; ``results`` is the same list referenced from
    ``merged_data['results']``.
    """

    for entry in results:
        citation = entry.get("source_citation")
        if isinstance(citation, dict):
            citation = dict(citation)
            citation["source_type"] = source_type
            citation["source_id"] = document_id
            entry["source_citation"] = citation
        else:
            entry["source_citation"] = {
                "source_type": source_type,
                "source_id": document_id,
            }


def _merge_intake(parsed_pages: list[BaseModel]) -> dict[str, Any]:
    """Concatenate list sections, prefer first non-None scalars."""

    merged: dict[str, Any] = {
        "demographics": None,
        "chief_concern": None,
        "current_medications": [],
        "allergies": [],
        "family_history": [],
        "social_history": None,
    }
    for page in parsed_pages:
        page_dict = page.model_dump()
        if merged["demographics"] is None:
            merged["demographics"] = page_dict.get("demographics")
        if merged["chief_concern"] is None and page_dict.get("chief_concern"):
            merged["chief_concern"] = page_dict["chief_concern"]
        if merged["social_history"] is None:
            merged["social_history"] = page_dict.get("social_history")
        for list_key in ("current_medications", "allergies", "family_history"):
            entries = page_dict.get(list_key) or []
            merged[list_key].extend(entries)

    # Required fields — fall back to empty defaults if none of the pages
    # populated them. ``demographics`` cannot be None (schema requires it),
    # ``chief_concern`` cannot be empty.
    if merged["demographics"] is None:
        merged["demographics"] = {}
    if not merged["chief_concern"]:
        merged["chief_concern"] = "(not stated on form)"
    return merged
