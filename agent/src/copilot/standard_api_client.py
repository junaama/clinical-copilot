"""Client for OpenEMR's Standard (non-FHIR) REST API.

Provides typed methods for write operations that the FHIR API does not
support: document uploads, allergy creation, medication creation, and
medical problem creation.

Uses the same bearer token as ``FhirClient`` (resolved from the active
SMART context or the static ``OPENEMR_FHIR_TOKEN`` env var). Follows
the same ``(ok, id_or_none, error_or_none, latency_ms)`` return pattern
used by FhirClient.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from .config import Settings

# Transient-only retry: 3 attempts with exponential backoff on connect/read
# failures. Server returning 4xx (auth, validation) is NOT retried.
_HTTPX_RETRY_TRANSPORT = httpx.AsyncHTTPTransport(retries=3)


def strip_fhir_prefix(reference: str) -> str:
    """Return the bare ID from a FHIR-style reference like ``Patient/<uuid>``.

    OpenEMR's Standard REST API path expects bare IDs, but the agent's
    conversation state and tool inputs frequently carry the FHIR-style
    ``<Type>/<id>`` form. Slotting that into a URL produces
    ``.../patient/Patient/<uuid>/...`` which 404s. Idempotent on bare IDs.
    """
    return reference.split("/", 1)[1] if "/" in reference else reference


class StandardApiClient:
    """Async wrapper around OpenEMR's Standard REST API for write operations."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=30.0, transport=_HTTPX_RETRY_TRANSPORT)

    def _resolve_token(self) -> str:
        # Lazy import to avoid an import cycle (tools imports fhir).
        from .tools import get_active_smart_token

        return get_active_smart_token() or self._settings.openemr_fhir_token.get_secret_value()

    @property
    def _base_url(self) -> str:
        """Standard API base URL: ``<openemr_base_url>/apis/default/api``."""
        return f"{self._settings.openemr_base_url.rstrip('/')}/apis/default/api"

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
        }

    async def upload_document(
        self,
        patient_id: str,
        file_data: bytes,
        filename: str,
        category: str = "uncategorized",
    ) -> tuple[bool, str | None, str | None, int]:
        """Upload a document to a patient's chart.

        POST /api/patient/{pid}/document

        Returns ``(ok, document_id, error, latency_ms)``.
        """
        started = time.monotonic()
        token = self._resolve_token()
        if not token:
            return False, None, "no_token", int((time.monotonic() - started) * 1000)

        url = f"{self._base_url}/patient/{strip_fhir_prefix(patient_id)}/document"
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

    async def create_allergy(
        self,
        patient_id: str,
        allergy_data: dict[str, Any],
    ) -> tuple[bool, str | None, str | None, int]:
        """Create an allergy record for a patient.

        POST /api/patient/{pid}/allergy

        ``allergy_data`` should contain at minimum:
        - ``title``: allergy description
        - ``begdate``: onset date (optional)
        - ``severity_al``: severity (optional)

        Returns ``(ok, allergy_id, error, latency_ms)``.
        """
        started = time.monotonic()
        token = self._resolve_token()
        if not token:
            return False, None, "no_token", int((time.monotonic() - started) * 1000)

        url = f"{self._base_url}/patient/{strip_fhir_prefix(patient_id)}/allergy"
        headers = {**self._headers(token), "Content-Type": "application/json"}

        try:
            response = await self._client.post(
                url, headers=headers, json=allergy_data
            )
        except httpx.HTTPError as exc:
            return False, None, f"transport: {exc.__class__.__name__}", int(
                (time.monotonic() - started) * 1000
            )

        latency = int((time.monotonic() - started) * 1000)
        if response.status_code not in (200, 201):
            return False, None, f"http_{response.status_code}", latency

        body = response.json()
        allergy_id = str(body.get("id") or body.get("pid") or "")
        return True, allergy_id or None, None, latency

    async def create_medication(
        self,
        patient_id: str,
        medication_data: dict[str, Any],
    ) -> tuple[bool, str | None, str | None, int]:
        """Create a medication record for a patient.

        POST /api/patient/{pid}/medication

        ``medication_data`` should contain at minimum:
        - ``title``: medication name
        - ``dosage``: dosage information (optional)
        - ``begdate``: start date (optional)

        Returns ``(ok, medication_id, error, latency_ms)``.
        """
        started = time.monotonic()
        token = self._resolve_token()
        if not token:
            return False, None, "no_token", int((time.monotonic() - started) * 1000)

        url = f"{self._base_url}/patient/{strip_fhir_prefix(patient_id)}/medication"
        headers = {**self._headers(token), "Content-Type": "application/json"}

        try:
            response = await self._client.post(
                url, headers=headers, json=medication_data
            )
        except httpx.HTTPError as exc:
            return False, None, f"transport: {exc.__class__.__name__}", int(
                (time.monotonic() - started) * 1000
            )

        latency = int((time.monotonic() - started) * 1000)
        if response.status_code not in (200, 201):
            return False, None, f"http_{response.status_code}", latency

        body = response.json()
        med_id = str(body.get("id") or body.get("pid") or "")
        return True, med_id or None, None, latency

    async def create_medical_problem(
        self,
        patient_id: str,
        problem_data: dict[str, Any],
    ) -> tuple[bool, str | None, str | None, int]:
        """Create a medical problem record for a patient.

        POST /api/patient/{pid}/medical_problem

        ``problem_data`` should contain at minimum:
        - ``title``: problem description
        - ``begdate``: onset date (optional)

        Returns ``(ok, problem_id, error, latency_ms)``.
        """
        started = time.monotonic()
        token = self._resolve_token()
        if not token:
            return False, None, "no_token", int((time.monotonic() - started) * 1000)

        url = f"{self._base_url}/patient/{strip_fhir_prefix(patient_id)}/medical_problem"
        headers = {**self._headers(token), "Content-Type": "application/json"}

        try:
            response = await self._client.post(
                url, headers=headers, json=problem_data
            )
        except httpx.HTTPError as exc:
            return False, None, f"transport: {exc.__class__.__name__}", int(
                (time.monotonic() - started) * 1000
            )

        latency = int((time.monotonic() - started) * 1000)
        if response.status_code not in (200, 201):
            return False, None, f"http_{response.status_code}", latency

        body = response.json()
        problem_id = str(body.get("id") or body.get("pid") or "")
        return True, problem_id or None, None, latency
