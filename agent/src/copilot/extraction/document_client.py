"""Document client for OpenEMR's Standard REST API.

Wraps the three patient-document endpoints used by the extraction pipeline:

* ``POST /api/patient/{pid}/document``    — upload
* ``GET  /api/patient/{pid}/document``    — list
* ``GET  /api/patient/{pid}/document/{did}`` — download

Mirrors the ``(ok, ..., error_or_none, latency_ms)`` return contract used by
``FhirClient`` and ``StandardApiClient`` so call sites can write one error-
handling pattern across HTTP boundaries.

Authorization context:

The OpenEMR-side endpoints already enforce authorization via the bearer
token's scope. The application-level CareTeam gate is enforced *one layer
up* (in the extraction tools, issue 006), the same place where the gate is
enforced for FHIR reads. This client deliberately stays a thin HTTP wrapper
so it remains easy to mock, exactly like ``StandardApiClient``.

Defensive validation done before any HTTP call:

* Magic-byte sniff: PDF (``%PDF-``), PNG (``\\x89PNG\\r\\n\\x1a\\n``), JPEG
  (``\\xff\\xd8\\xff``). Anything else returns ``invalid_file_type`` without
  hitting the network — protects OpenEMR from non-medical-document uploads
  and keeps the extraction pipeline's input alphabet narrow.
* Size cap of 20 MB. The agent's VLM extraction step doesn't usefully scale
  past this, and we'd rather refuse fast than wait for OpenEMR's reverse
  proxy to 413 us.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from copilot.config import Settings
from copilot.standard_api_client import strip_fhir_prefix

_log = logging.getLogger(__name__)

# 20 MB. Anything beyond is rejected client-side. Exposed as a constant so
# tests can construct edge-of-limit fixtures without re-deriving the number.
MAX_DOCUMENT_BYTES: int = 20 * 1024 * 1024

_PDF_MAGIC = b"%PDF-"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"

# OpenEMR's Standard API document upload routes the category through
# ``?path=<category-name>`` (see ``apis/routes/_rest_routes_standard.inc.php``
# ``POST /api/patient/:pid/document``). The category must already exist in
# the ``categories`` tree. Default categories include "Lab Report" and
# "Medical Record" (sql/database.sql:305-308). We map our ``doc_type`` to
# those defaults so a fresh OpenEMR install accepts uploads without
# operator-side category creation.
_DEFAULT_CATEGORY_PATH = "Medical Record"
_DOC_TYPE_TO_CATEGORY_PATH: dict[str, str] = {
    "lab_pdf": "Lab Report",
    "intake_form": "Medical Record",
}

# Same retry transport contract used by FhirClient / StandardApiClient: 3
# attempts on transport-layer failures only. 4xx responses pass straight
# through.
_HTTPX_RETRY_TRANSPORT = httpx.AsyncHTTPTransport(retries=3)

_RECOVERY_WINDOW_SECONDS = 60.0
UPLOAD_LANDED_ID_LOST = "upload_landed_id_lost"

# In-process bytes cache used to short-circuit ``download()`` for documents
# we *just* uploaded in the same process. Motivation: OpenEMR's Standard-
# API GET ``/api/patient/{pid}/document/{did}`` route runs through the
# legacy C_Document UI controller, which leaks UI-side concerns (CSRF
# token, session wrapper API) into the API path and 500s. While that's
# being fixed upstream-of-us, we keep the bytes from the upload step and
# serve them to the very-next ``extract_document`` call without a round
# trip.
#
# **Cache is module-level on purpose.** ``server.py``'s ``/upload`` route
# resolves a DocumentClient on ``app.state.document_client`` (one instance
# per app); ``tools/__init__.py``'s ``_build_extraction_tools`` builds a
# *separate* DocumentClient when it wires the extraction tools into the
# chat graph. Without a shared module-level cache, the upload populates
# instance A's cache, the extract reads from instance B's empty cache,
# and we still 500 through the broken OpenEMR path. Module-level dict is
# safe here because the agent runs single-replica and CPython dict ops
# are atomic across coroutines on the same loop.
_UPLOAD_CACHE_MAX_ENTRIES: int = 8
_UPLOAD_CACHE_TTL_SECONDS: float = 60 * 60  # 1 hour


@dataclass(frozen=True)
class _UploadCacheEntry:
    file_bytes: bytes
    mimetype: str
    cached_at: float


_UPLOAD_BYTES_CACHE: OrderedDict[str, _UploadCacheEntry] = OrderedDict()


def _infer_mimetype(file_data: bytes) -> str:
    """Map a known magic-byte signature to its IANA media type.

    Mirrors the magic-byte check in ``_is_supported_document`` so the
    download cache returns the same content-type the VLM dispatcher would
    have inferred from a real OpenEMR response.
    """
    if file_data.startswith(_PDF_MAGIC):
        return "application/pdf"
    if file_data.startswith(_PNG_MAGIC):
        return "image/png"
    if file_data.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    return "application/octet-stream"


def _is_supported_document(file_data: bytes) -> bool:
    """Return True iff ``file_data`` starts with a PDF/PNG/JPEG magic-byte sequence."""
    if file_data.startswith(_PDF_MAGIC):
        return True
    if file_data.startswith(_PNG_MAGIC):
        return True
    if file_data.startswith(_JPEG_MAGIC):
        return True
    return False


def _is_bool_given_upload_serializer_failure(response: httpx.Response) -> bool:
    body_text = response.text or ""
    return (
        response.status_code == 500
        and "bool given" in body_text
        and "getResponseForPayload" in body_text
    )


def _document_filename(document: dict[str, Any]) -> str:
    raw = (
        document.get("name")
        or document.get("filename")
        or document.get("document_name")
        or document.get("file_name")
        or document.get("title")
        or ""
    )
    return str(raw)


def _normalize_filename(filename: str) -> str:
    return filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip().lower()


def _filename_fingerprint(filename: str) -> str:
    return hashlib.sha256(_normalize_filename(filename).encode()).hexdigest()[:12]


def _document_id(document: dict[str, Any]) -> str:
    raw = (
        document.get("id")
        or document.get("document_id")
        or document.get("uuid")
        or document.get("pid")
        or ""
    )
    return str(raw)


def _parse_document_timestamp(document: dict[str, Any]) -> datetime | None:
    raw = (
        document.get("date")
        or document.get("docdate")
        or document.get("created_at")
        or document.get("create_date")
        or document.get("created")
        or document.get("modified")
        or document.get("updated_at")
        or document.get("upload_date")
    )
    if raw is None:
        return None
    if isinstance(raw, int | float):
        return datetime.fromtimestamp(float(raw), tz=UTC)
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _find_recent_filename_match(
    documents: list[dict[str, Any]],
    filename: str,
    attempted_at: datetime,
) -> str | None:
    matches: list[tuple[datetime, str]] = []
    untimed_matches: list[str] = []
    attempted_at = attempted_at.astimezone(UTC)
    expected_filename = _normalize_filename(filename)
    for document in documents:
        if _normalize_filename(_document_filename(document)) != expected_filename:
            continue
        doc_id = _document_id(document)
        if not doc_id:
            continue
        timestamp = _parse_document_timestamp(document)
        if timestamp is None:
            untimed_matches.append(doc_id)
            continue
        # Older OpenEMR document-list responses only expose ``docdate``
        # (YYYY-MM-DD). Treat same-day date-only matches as recoverable:
        # the upload route already scoped the list by patient, category,
        # and filename, and rejecting every date-only row makes successful
        # uploads appear broken for most of the day.
        is_date_only = (
            timestamp.hour == 0
            and timestamp.minute == 0
            and timestamp.second == 0
            and timestamp.microsecond == 0
        )
        if is_date_only and timestamp.date() == attempted_at.date():
            matches.append((timestamp, doc_id))
            continue
        if abs((timestamp - attempted_at).total_seconds()) > _RECOVERY_WINDOW_SECONDS:
            continue
        matches.append((timestamp, doc_id))
    if not matches:
        # Some OpenEMR document-list variants return id + filename with no
        # timestamp at all. The fallback list is already scoped by patient,
        # category, and exact filename immediately after the upload-landed
        # serializer failure, so returning the highest numeric id is safer
        # than reporting a false failure and blocking document RAG.
        if untimed_matches:
            return max(untimed_matches, key=_document_id_sort_key)
        return None
    matches.sort(key=lambda item: (item[0], _document_id_sort_key(item[1])), reverse=True)
    return matches[0][1]


def _document_id_sort_key(document_id: str) -> tuple[int, str]:
    if document_id.isdigit():
        return int(document_id), document_id
    return 0, document_id


def _log_upload_recovery_failure(
    *,
    patient_id: str,
    filename: str,
    category: str,
    documents: list[dict[str, Any]],
    list_ok: bool,
    list_error: str | None,
) -> None:
    expected = _normalize_filename(filename)
    matching = [
        document
        for document in documents
        if _normalize_filename(_document_filename(document)) == expected
    ]
    sample = [
        {
            "has_id": bool(_document_id(document)),
            "filename_hash": _filename_fingerprint(_document_filename(document)),
            "timestamp_present": _parse_document_timestamp(document) is not None,
            "keys": sorted(str(key) for key in document.keys())[:8],
        }
        for document in documents[:5]
    ]
    _log.warning(
        "upload id recovery failed: patient_id_hash=%s category=%s "
        "filename_hash=%s list_ok=%s list_error=%s document_count=%d "
        "matching_filename_count=%d sample=%s",
        hashlib.sha256(patient_id.encode()).hexdigest()[:12],
        category,
        _filename_fingerprint(filename),
        list_ok,
        list_error,
        len(documents),
        len(matching),
        sample,
    )


class DocumentClient:
    """Async wrapper around OpenEMR's Standard-API document endpoints."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=30.0, transport=_HTTPX_RETRY_TRANSPORT)

    @staticmethod
    def _cache_key(patient_id: str, document_id: str) -> str:
        return f"{strip_fhir_prefix(patient_id)}:{strip_fhir_prefix(document_id)}"

    def _cache_set(self, patient_id: str, document_id: str, file_bytes: bytes) -> None:
        if not document_id:
            return
        key = self._cache_key(patient_id, document_id)
        entry = _UploadCacheEntry(
            file_bytes=file_bytes,
            mimetype=_infer_mimetype(file_bytes),
            cached_at=time.monotonic(),
        )
        _UPLOAD_BYTES_CACHE[key] = entry
        _UPLOAD_BYTES_CACHE.move_to_end(key)
        while len(_UPLOAD_BYTES_CACHE) > _UPLOAD_CACHE_MAX_ENTRIES:
            _UPLOAD_BYTES_CACHE.popitem(last=False)

    def _cache_get(
        self, patient_id: str, document_id: str
    ) -> _UploadCacheEntry | None:
        key = self._cache_key(patient_id, document_id)
        entry = _UPLOAD_BYTES_CACHE.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry.cached_at > _UPLOAD_CACHE_TTL_SECONDS:
            _UPLOAD_BYTES_CACHE.pop(key, None)
            return None
        _UPLOAD_BYTES_CACHE.move_to_end(key)
        return entry

    def _resolve_token(self) -> str:
        # Lazy import mirrors StandardApiClient — ``copilot.tools`` imports
        # ``copilot.fhir``, which would create a cycle if we imported at
        # module load.
        from copilot.tools import get_active_smart_token

        return (
            get_active_smart_token()
            or self._settings.openemr_fhir_token.get_secret_value()
        )

    @property
    def _base_url(self) -> str:
        """Standard API base URL: ``<openemr_base_url>/apis/default/api``."""
        return f"{self._settings.openemr_base_url.rstrip('/')}/apis/default/api"

    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    # ------------------------------------------------------------------
    # upload
    # ------------------------------------------------------------------

    async def upload(
        self,
        patient_id: str,
        file_data: bytes,
        filename: str,
        category: str = "uncategorized",
    ) -> tuple[bool, str | None, str | None, int]:
        """Upload a document to a patient's chart.

        Returns ``(ok, document_id, error, latency_ms)``. Error sentinels:

        * ``invalid_file_type`` — magic bytes don't match PDF/PNG/JPEG
        * ``file_too_large``   — payload exceeds ``MAX_DOCUMENT_BYTES``
        * ``no_token``         — neither SMART context nor static token set
        * ``upload_landed_id_lost`` — OpenEMR saved the file but the real id
          could not be recovered from a same-category list call
        * ``http_<status>``    — server returned a non-2xx status
        * ``transport: <Exc>`` — httpx raised at the transport layer
        """
        started = time.monotonic()
        attempted_at = datetime.now(UTC)

        if len(file_data) > MAX_DOCUMENT_BYTES:
            return False, None, "file_too_large", int((time.monotonic() - started) * 1000)
        if not _is_supported_document(file_data):
            return False, None, "invalid_file_type", int((time.monotonic() - started) * 1000)

        token = self._resolve_token()
        if not token:
            return False, None, "no_token", int((time.monotonic() - started) * 1000)

        url = f"{self._base_url}/patient/{strip_fhir_prefix(patient_id)}/document"
        headers = self._headers(token)
        files = {"document": (filename, file_data)}
        # OpenEMR reads the destination category from ``?path=`` (query
        # string), not a multipart form field. Map our doc_type to a
        # known-default category so the upload lands without requiring
        # operator-side category creation.
        category_path = _DOC_TYPE_TO_CATEGORY_PATH.get(category, _DEFAULT_CATEGORY_PATH)
        params = {"path": category_path}

        try:
            response = await self._client.post(
                url, headers=headers, params=params, files=files
            )
        except httpx.HTTPError as exc:
            return False, None, f"transport: {exc.__class__.__name__}", int(
                (time.monotonic() - started) * 1000
            )

        latency = int((time.monotonic() - started) * 1000)
        if response.status_code not in (200, 201):
            body_text = response.text or ""
            # Workaround for upstream openemr/openemr:latest bug:
            # ``DocumentRestController::postWithPath`` returns the bool
            # from ``DocumentService::insertAtPath()`` to a version of
            # ``RestControllerHelper::getResponseForPayload`` that
            # doesn't accept bool — even when the upload itself
            # succeeded (``Document::createDocument`` returned empty).
            # When the response body matches that exact signature, recover
            # the real OpenEMR document id from a same-category list call.
            if _is_bool_given_upload_serializer_failure(response):
                return await self._recover_landed_upload_id(
                    patient_id=patient_id,
                    file_data=file_data,
                    category=category,
                    filename=filename,
                    attempted_at=attempted_at,
                    started=started,
                )
            # Surface a snippet of the response body so 500s aren't a
            # black box. Truncate to keep the audit log line bounded.
            snippet = body_text[:200].replace("\n", " ").strip()
            return False, None, f"http_{response.status_code}: {snippet}", latency

        body = response.json()
        if body is True:
            return await self._recover_landed_upload_id(
                patient_id=patient_id,
                file_data=file_data,
                category=category,
                filename=filename,
                attempted_at=attempted_at,
                started=started,
            )
        if not isinstance(body, dict):
            return False, None, f"unexpected_response: {type(body).__name__}", latency

        doc_id = str(body.get("id") or body.get("pid") or "")
        if doc_id:
            self._cache_set(patient_id, doc_id, file_data)
        return True, doc_id or None, None, latency

    async def _recover_landed_upload_id(
        self,
        *,
        patient_id: str,
        file_data: bytes,
        category: str,
        filename: str,
        attempted_at: datetime,
        started: float,
    ) -> tuple[bool, str | None, str | None, int]:
        ok, documents, list_error, _list_latency = await self.list(
            patient_id, category=category
        )
        recovered_id = (
            _find_recent_filename_match(documents, filename, attempted_at)
            if ok
            else None
        )
        total_latency = int((time.monotonic() - started) * 1000)
        if recovered_id:
            self._cache_set(patient_id, recovered_id, file_data)
            return True, recovered_id, None, total_latency
        _log_upload_recovery_failure(
            patient_id=patient_id,
            filename=filename,
            category=category,
            documents=documents,
            list_ok=ok,
            list_error=list_error,
        )
        return False, None, UPLOAD_LANDED_ID_LOST, total_latency

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------

    async def list(
        self,
        patient_id: str,
        category: str | None = None,
    ) -> tuple[bool, list[dict[str, Any]], str | None, int]:
        """List documents in a patient's chart, optionally filtered by category.

        Returns ``(ok, documents, error, latency_ms)``. ``documents`` is the
        decoded JSON array straight from the server; we deliberately do not
        re-shape it here so callers can evolve the schema without breaking
        this client.
        """
        started = time.monotonic()
        token = self._resolve_token()
        if not token:
            return False, [], "no_token", int((time.monotonic() - started) * 1000)

        url = f"{self._base_url}/patient/{strip_fhir_prefix(patient_id)}/document"
        # OpenEMR's GET route reads the category from ``?path=`` (same
        # contract as POST). Map ``doc_type``-style values to known
        # default categories; pass through anything else (lets callers
        # query an arbitrary OpenEMR category by name).
        category_path = (
            _DOC_TYPE_TO_CATEGORY_PATH.get(category, category)
            if category
            else None
        )
        params = {"path": category_path} if category_path else None

        try:
            response = await self._client.get(
                url, headers=self._headers(token), params=params
            )
        except httpx.HTTPError as exc:
            return False, [], f"transport: {exc.__class__.__name__}", int(
                (time.monotonic() - started) * 1000
            )

        latency = int((time.monotonic() - started) * 1000)
        if response.status_code != 200:
            return False, [], f"http_{response.status_code}", latency

        body = response.json()
        docs: list[dict[str, Any]] = body if isinstance(body, list) else []
        return True, docs, None, latency

    # ------------------------------------------------------------------
    # download
    # ------------------------------------------------------------------

    async def download(
        self,
        patient_id: str,
        document_id: str,
    ) -> tuple[bool, bytes | None, str | None, str | None, int]:
        """Fetch raw document bytes plus mimetype.

        Accepts either bare IDs or FHIR-style references — callers
        upstream pass ``Patient/<uuid>`` and ``DocumentReference/<id>``,
        and the OpenEMR Standard API URL needs the bare IDs only.

        Returns ``(ok, file_bytes, mimetype, error, latency_ms)``. ``mimetype``
        is the bare media type (``application/pdf``) with any ``charset=``
        suffix stripped — the VLM dispatcher keys off the bare type.
        """
        started = time.monotonic()

        # Short-circuit on cache hit: skips a broken OpenEMR Standard-API
        # round trip for documents we *just* uploaded (see _UPLOAD_CACHE
        # rationale on the module-level constants).
        cached = self._cache_get(patient_id, document_id)
        if cached is not None:
            return (
                True,
                cached.file_bytes,
                cached.mimetype,
                None,
                int((time.monotonic() - started) * 1000),
            )

        token = self._resolve_token()
        if not token:
            return (
                False,
                None,
                None,
                "no_token",
                int((time.monotonic() - started) * 1000),
            )

        url = (
            f"{self._base_url}/patient/{strip_fhir_prefix(patient_id)}"
            f"/document/{strip_fhir_prefix(document_id)}"
        )

        try:
            response = await self._client.get(url, headers=self._headers(token))
        except httpx.HTTPError as exc:
            return (
                False,
                None,
                None,
                f"transport: {exc.__class__.__name__}",
                int((time.monotonic() - started) * 1000),
            )

        latency = int((time.monotonic() - started) * 1000)
        if response.status_code != 200:
            return False, None, None, f"http_{response.status_code}", latency

        raw_ct = response.headers.get("content-type", "application/octet-stream")
        mimetype = raw_ct.split(";", 1)[0].strip()
        # Populate the cache from a real round trip too — same-conversation
        # re-extracts (e.g. user re-asks about the same document) avoid a
        # second hit even before any upload-side path put the bytes there.
        self._cache_set(patient_id, document_id, response.content)
        return True, response.content, mimetype, None, latency
