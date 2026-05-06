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

import time
from typing import Any

import httpx

from copilot.config import Settings

# 20 MB. Anything beyond is rejected client-side. Exposed as a constant so
# tests can construct edge-of-limit fixtures without re-deriving the number.
MAX_DOCUMENT_BYTES: int = 20 * 1024 * 1024

_PDF_MAGIC = b"%PDF-"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"

# Same retry transport contract used by FhirClient / StandardApiClient: 3
# attempts on transport-layer failures only. 4xx responses pass straight
# through.
_HTTPX_RETRY_TRANSPORT = httpx.AsyncHTTPTransport(retries=3)


def _is_supported_document(file_data: bytes) -> bool:
    """Return True iff ``file_data`` starts with a PDF/PNG/JPEG magic-byte sequence."""
    if file_data.startswith(_PDF_MAGIC):
        return True
    if file_data.startswith(_PNG_MAGIC):
        return True
    if file_data.startswith(_JPEG_MAGIC):
        return True
    return False


class DocumentClient:
    """Async wrapper around OpenEMR's Standard-API document endpoints."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=30.0, transport=_HTTPX_RETRY_TRANSPORT)

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
        * ``http_<status>``    — server returned a non-2xx status
        * ``transport: <Exc>`` — httpx raised at the transport layer
        """
        started = time.monotonic()

        if len(file_data) > MAX_DOCUMENT_BYTES:
            return False, None, "file_too_large", int((time.monotonic() - started) * 1000)
        if not _is_supported_document(file_data):
            return False, None, "invalid_file_type", int((time.monotonic() - started) * 1000)

        token = self._resolve_token()
        if not token:
            return False, None, "no_token", int((time.monotonic() - started) * 1000)

        url = f"{self._base_url}/patient/{patient_id}/document"
        headers = self._headers(token)
        files = {"document": (filename, file_data)}
        data = {"category": category}

        try:
            response = await self._client.post(
                url, headers=headers, files=files, data=data
            )
        except httpx.HTTPError as exc:
            return False, None, f"transport: {exc.__class__.__name__}", int(
                (time.monotonic() - started) * 1000
            )

        latency = int((time.monotonic() - started) * 1000)
        if response.status_code not in (200, 201):
            return False, None, f"http_{response.status_code}", latency

        body = response.json()
        doc_id = str(body.get("id") or body.get("pid") or "")
        return True, doc_id or None, None, latency

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

        url = f"{self._base_url}/patient/{patient_id}/document"
        params = {"category": category} if category else None

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

        Returns ``(ok, file_bytes, mimetype, error, latency_ms)``. ``mimetype``
        is the bare media type (``application/pdf``) with any ``charset=``
        suffix stripped — the VLM dispatcher keys off the bare type.
        """
        started = time.monotonic()
        token = self._resolve_token()
        if not token:
            return (
                False,
                None,
                None,
                "no_token",
                int((time.monotonic() - started) * 1000),
            )

        url = f"{self._base_url}/patient/{patient_id}/document/{document_id}"

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
        return True, response.content, mimetype, None, latency
