"""Document-extraction tools — the LLM-facing surface for issue 006.

Three tools wired into the LangChain tool-call surface:

* ``attach_document(patient_id, file_path, doc_type)`` — read a file
  from disk and upload it to OpenEMR via ``DocumentClient``.
* ``list_patient_documents(patient_id, category)`` — list documents
  attached to a patient.
* ``extract_document(patient_id, document_id, doc_type)`` — full
  pipeline: download → VLM extract → bbox match → persist.

Every tool runs through the same ``CareTeamGate`` used by the granular
FHIR readers (``_enforce_patient_authorization``). Persistence
dependencies (DocumentClient, model, store, persister) are injected
at construction time so the tools are testable end-to-end with fakes.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from langchain_core.tools import StructuredTool

from ..extraction.bbox_matcher import match_extraction_to_bboxes
from ..extraction.vlm import extract_document as vlm_extract_document
from .helpers import _enforce_patient_authorization

_log = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from langchain_core.language_models.chat_models import BaseChatModel

    from ..care_team import CareTeamGate
    from ..extraction.persistence import DocumentExtractionStore, IntakePersister


_VALID_DOC_TYPES: frozenset[str] = frozenset({"lab_pdf", "intake_form"})


@runtime_checkable
class DocumentClientLike(Protocol):
    """Subset of ``DocumentClient`` (issue 003) consumed by these tools."""

    async def upload(
        self,
        patient_id: str,
        file_data: bytes,
        filename: str,
        category: str,
    ) -> tuple[bool, str | None, str | None, int]: ...

    async def list(
        self,
        patient_id: str,
        category: str | None = None,
    ) -> tuple[bool, list[dict[str, Any]], str | None, int]: ...

    async def download(
        self,
        patient_id: str,
        document_id: str,
    ) -> tuple[bool, bytes | None, str | None, str | None, int]: ...


def _validate_doc_type(doc_type: str) -> str | None:
    if doc_type not in _VALID_DOC_TYPES:
        return (
            f"invalid doc_type '{doc_type}'. "
            f"Expected one of: {sorted(_VALID_DOC_TYPES)}"
        )
    return None


def _error_envelope(error: str, latency_ms: int = 0) -> dict[str, Any]:
    return {
        "ok": False,
        "error": error,
        "latency_ms": latency_ms,
    }


def _cache_row_envelope(
    row: dict[str, Any],
    *,
    cache_key: str,
    latency_ms: int,
) -> dict[str, Any]:
    document_id = str(row.get("document_id") or "")
    return {
        "ok": True,
        "cache_hit": True,
        "cache_key": cache_key,
        "doc_type": row.get("doc_type"),
        "document_id": document_id,
        "document_ref": f"DocumentReference/{document_id}",
        "extraction_id": row.get("id"),
        "extraction": row.get("extraction_json") or {},
        "bboxes": row.get("bboxes_json") or [],
        "intake_summary": None,
        "pages_processed": None,
        "latency_ms": latency_ms,
    }


async def _lookup_cache_by_document_id(
    store: Any,
    *,
    patient_id: str,
    document_id: str,
) -> dict[str, Any] | None:
    lookup = getattr(store, "get_latest_by_document_id", None)
    if lookup is None:
        return None
    return await lookup(patient_id=patient_id, document_id=document_id)


async def _lookup_cache_by_hash(
    store: Any,
    *,
    patient_id: str,
    filename: str | None,
    content_sha256: str | None,
) -> dict[str, Any] | None:
    if not filename or not content_sha256:
        return None
    lookup = getattr(store, "get_latest_by_hash", None)
    if lookup is None:
        return None
    return await lookup(
        patient_id=patient_id,
        filename=filename,
        content_sha256=content_sha256,
    )


def _emit_cache_observability(
    *,
    cache_hit: bool,
    cache_key: str,
    doc_type: str,
) -> None:
    metadata = {
        "cache_hit": cache_hit,
        "cache_key": cache_key,
        "doc_type": doc_type,
    }
    _log.info("extract_document cache lookup", extra=metadata)
    try:
        import langfuse  # type: ignore[import-not-found]

        get_client = getattr(langfuse, "get_client", None)
        client = get_client() if callable(get_client) else None
        update_span = getattr(client, "update_current_span", None)
        if callable(update_span):
            update_span(metadata=metadata)
    except Exception:  # pragma: no cover - observability must be best-effort
        _log.debug("extract_document cache span update skipped", exc_info=True)


def make_extraction_tools(
    *,
    gate: CareTeamGate,
    document_client: DocumentClientLike,
    vlm_model: BaseChatModel,
    store: DocumentExtractionStore,
    persister: IntakePersister,
    extraction_model_name: str = "claude-sonnet-4-6",
) -> list[StructuredTool]:
    """Build the three extraction tools.

    The VLM model is the long-lived ``BaseChatModel`` from
    ``copilot.llm.build_vision_model(settings)``. Tests inject a stub
    that satisfies ``with_structured_output(...).ainvoke(messages)``.
    """

    async def attach_document(
        patient_id: str,
        file_path: str,
        doc_type: str,
    ) -> dict[str, Any]:
        """Upload a local file to OpenEMR's document store for ``patient_id``.

        ``doc_type`` must be ``lab_pdf`` or ``intake_form`` — the doc
        type is recorded with the upload and used when ``extract_document``
        is called against the resulting document_id later.
        """
        started = time.monotonic()
        if (err := _validate_doc_type(doc_type)) is not None:
            return _error_envelope(err, _ms(started))
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied

        path = Path(file_path)
        try:
            file_data = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            return _error_envelope(
                f"file_read_error: {exc.__class__.__name__}", _ms(started)
            )

        ok, doc_id, err, _latency = await document_client.upload(
            patient_id,
            file_data,
            path.name,
            doc_type,
        )
        if not ok:
            return _error_envelope(err or "upload_failed", _ms(started))

        return {
            "ok": True,
            "document_id": doc_id,
            "document_ref": f"DocumentReference/{doc_id}",
            "doc_type": doc_type,
            "filename": path.name,
            "size_bytes": len(file_data),
            "latency_ms": _ms(started),
        }

    async def list_patient_documents(
        patient_id: str,
        category: str | None = None,
    ) -> dict[str, Any]:
        """List documents in ``patient_id``'s chart, optionally filtered by category."""
        started = time.monotonic()
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied

        ok, documents, err, _latency = await document_client.list(
            patient_id, category
        )
        if not ok:
            return _error_envelope(err or "list_failed", _ms(started))

        # Stamp each row with a canonical ``document_ref`` so the
        # supervisor's intake_extractor worker (issue 009) can scrape it
        # into ``fetched_refs`` for the verifier.
        documents_list = list(documents or [])
        for d in documents_list:
            doc_id = d.get("id") or d.get("document_id")
            if doc_id and "document_ref" not in d:
                d["document_ref"] = f"DocumentReference/{doc_id}"
        return {
            "ok": True,
            "documents": documents_list,
            "count": len(documents_list),
            "latency_ms": _ms(started),
        }

    async def extract_document(
        patient_id: str,
        document_id: str,
        doc_type: str,
        filename: str | None = None,
        content_sha256: str | None = None,
    ) -> dict[str, Any]:
        """Run the full pipeline: download → VLM → bbox match → persist."""
        started = time.monotonic()
        if (err := _validate_doc_type(doc_type)) is not None:
            return _error_envelope(err, _ms(started))
        if (denied := await _enforce_patient_authorization(gate, patient_id)) is not None:
            return denied

        row = await _lookup_cache_by_document_id(
            store, patient_id=patient_id, document_id=document_id
        )
        if row is not None:
            cache_key = f"document_id:{document_id}"
            _emit_cache_observability(
                cache_hit=True,
                cache_key=cache_key,
                doc_type=doc_type,
            )
            return _cache_row_envelope(row, cache_key=cache_key, latency_ms=_ms(started))

        row = await _lookup_cache_by_hash(
            store,
            patient_id=patient_id,
            filename=filename,
            content_sha256=content_sha256,
        )
        if row is not None:
            cache_key = f"sha256:{content_sha256}"
            _emit_cache_observability(
                cache_hit=True,
                cache_key=cache_key,
                doc_type=doc_type,
            )
            return _cache_row_envelope(row, cache_key=cache_key, latency_ms=_ms(started))

        ok, file_data, mimetype, err, _latency = await document_client.download(
            patient_id, document_id
        )
        if not ok or file_data is None:
            return _error_envelope(err or "download_failed", _ms(started))

        resolved_sha256 = hashlib.sha256(file_data).hexdigest()
        row = await _lookup_cache_by_hash(
            store,
            patient_id=patient_id,
            filename=filename,
            content_sha256=resolved_sha256,
        )
        if row is not None:
            cache_key = f"sha256:{resolved_sha256}"
            _emit_cache_observability(
                cache_hit=True,
                cache_key=cache_key,
                doc_type=doc_type,
            )
            return _cache_row_envelope(row, cache_key=cache_key, latency_ms=_ms(started))

        _emit_cache_observability(
            cache_hit=False,
            cache_key=f"document_id:{document_id}",
            doc_type=doc_type,
        )

        try:
            result = await vlm_extract_document(
                file_data,
                mimetype or "application/octet-stream",
                doc_type,  # type: ignore[arg-type]
                document_id=f"DocumentReference/{document_id}",
                model=vlm_model,
                extraction_model_name=extraction_model_name,
            )
        except Exception as exc:
            return _error_envelope(
                f"vlm_extraction_failed: {exc.__class__.__name__}", _ms(started)
            )

        if not result.ok or result.extraction is None:
            return _error_envelope(
                f"vlm_extraction_failed: {result.error or 'no extraction'}",
                _ms(started),
            )

        extraction = result.extraction

        try:
            bboxes = match_extraction_to_bboxes(
                extraction, file_data, mimetype=mimetype
            )
        except Exception as exc:
            return _error_envelope(
                f"bbox_match_failed: {exc.__class__.__name__}", _ms(started)
            )

        intake_summary: dict[str, Any] | None = None
        try:
            if doc_type == "lab_pdf":
                extraction_id = await store.save_lab_extraction(
                    extraction=extraction,  # type: ignore[arg-type]
                    bboxes=bboxes,
                    document_id=document_id,
                    patient_id=patient_id,
                    filename=filename,
                    content_sha256=resolved_sha256,
                )
            else:  # intake_form
                summary = await persister.persist_intake(
                    patient_id=patient_id, extraction=extraction  # type: ignore[arg-type]
                )
                intake_summary = summary.to_dict()
                extraction_id = await store.save_intake_extraction(
                    extraction=extraction,  # type: ignore[arg-type]
                    bboxes=bboxes,
                    document_id=document_id,
                    patient_id=patient_id,
                    filename=filename,
                    content_sha256=resolved_sha256,
                )
        except Exception as exc:
            return _error_envelope(
                f"persistence_failed: {exc.__class__.__name__}", _ms(started)
            )

        return {
            "ok": True,
            "cache_hit": False,
            "cache_key": f"document_id:{document_id}",
            "doc_type": doc_type,
            "document_id": document_id,
            "document_ref": f"DocumentReference/{document_id}",
            "extraction_id": extraction_id,
            "extraction": extraction.model_dump(mode="json"),
            "bboxes": [b.model_dump(mode="json") for b in bboxes],
            "intake_summary": intake_summary,
            "pages_processed": result.pages_processed,
            "latency_ms": _ms(started),
        }

    return [
        StructuredTool.from_function(
            coroutine=attach_document,
            name="attach_document",
            description=(
                "Upload a local file (PDF, PNG, or JPEG) to OpenEMR's document "
                "store for a patient. Returns the document_id you can pass to "
                "extract_document. Args: patient_id, file_path (absolute path "
                "on the agent host), doc_type ('lab_pdf' or 'intake_form')."
            ),
        ),
        StructuredTool.from_function(
            coroutine=list_patient_documents,
            name="list_patient_documents",
            description=(
                "List documents attached to a patient's chart. Optional "
                "category filter (e.g. 'lab_pdf', 'intake_form'). Returns "
                "document metadata; pass an id to extract_document to read "
                "the actual content."
            ),
        ),
        StructuredTool.from_function(
            coroutine=extract_document,
            name="extract_document",
            description=(
                "Download a document from OpenEMR, run VLM extraction, "
                "compute bounding boxes, and persist results. Lab PDFs are "
                "stored in the agent's document_extractions table; intake "
                "forms additionally write allergies/medications/medical "
                "problems to OpenEMR via the Standard API. Args: patient_id, "
                "document_id (from attach_document or list_patient_documents), "
                "doc_type ('lab_pdf' or 'intake_form')."
            ),
        ),
    ]


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
