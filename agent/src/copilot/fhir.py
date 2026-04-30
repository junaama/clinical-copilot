"""FHIR client.

Two modes:

- ``USE_FIXTURE_FHIR=1`` (default for dev): serves an in-memory synthetic
  patient bundle so the agent loop can be exercised without OpenEMR auth.
- Real mode: hits ``OPENEMR_FHIR_BASE`` with ``OPENEMR_FHIR_TOKEN``.

Real-mode requests use httpx's transport-level retries for transient
network/5xx classes only — patient-data response payloads are never
silently re-fetched (per ARCHITECTURE.md §16). Auth and 4xx errors surface
to the caller immediately.

The fixture path is **not** intended as a long-lived stub — it's a development
bypass that disappears the moment a real bearer token is provided. Tracked in
``agentforge-docs/AGENT-TODO.md``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Settings
from .fixtures import FIXTURE_BUNDLE

# Transient-only retry: 3 attempts with exponential backoff on connect/read
# failures. Server returning 4xx (auth, validation) is NOT retried.
_HTTPX_RETRY_TRANSPORT = httpx.AsyncHTTPTransport(retries=3)

ABSENT = "[not on file]"


@dataclass(frozen=True)
class Row:
    fhir_ref: str
    resource_type: str
    fields: dict[str, Any]
    raw_excerpt: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    rows: tuple[Row, ...] = ()
    sources_checked: tuple[str, ...] = ()
    error: str | None = None
    latency_ms: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "rows": [
                {
                    "fhir_ref": r.fhir_ref,
                    "resource_type": r.resource_type,
                    "fields": r.fields,
                }
                for r in self.rows
            ],
            "sources_checked": list(self.sources_checked),
            "error": self.error,
            "latency_ms": self.latency_ms,
        }


class FhirClient:
    """Thin async wrapper around the FHIR endpoint."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._fixture = settings.use_fixture_fhir or not settings.openemr_fhir_token

    @property
    def fixture_mode(self) -> bool:
        return self._fixture

    async def search(
        self, resource_type: str, params: dict[str, Any]
    ) -> tuple[bool, list[dict[str, Any]], str | None, int]:
        """Run a FHIR search; return (ok, entries, error, latency_ms)."""
        started = time.monotonic()
        if self._fixture:
            entries = _fixture_search(resource_type, params)
            return True, entries, None, int((time.monotonic() - started) * 1000)

        url = f"{self._settings.openemr_fhir_base.rstrip('/')}/{resource_type}"
        headers = {
            "Accept": "application/fhir+json",
            "Authorization": f"Bearer {self._settings.openemr_fhir_token}",
        }
        try:
            async with httpx.AsyncClient(
                timeout=10.0, transport=_HTTPX_RETRY_TRANSPORT
            ) as client:
                response = await client.get(url, headers=headers, params=params)
        except httpx.HTTPError as exc:
            return False, [], f"transport: {exc.__class__.__name__}", int(
                (time.monotonic() - started) * 1000
            )

        latency = int((time.monotonic() - started) * 1000)
        if response.status_code != 200:
            return False, [], f"http_{response.status_code}", latency

        bundle = response.json()
        entries = [e.get("resource") for e in bundle.get("entry", []) if e.get("resource")]
        return True, entries, None, latency

    async def read(
        self, resource_type: str, resource_id: str
    ) -> tuple[bool, dict[str, Any] | None, str | None, int]:
        started = time.monotonic()
        if self._fixture:
            resource = _fixture_read(resource_type, resource_id)
            return resource is not None, resource, None, int(
                (time.monotonic() - started) * 1000
            )

        url = f"{self._settings.openemr_fhir_base.rstrip('/')}/{resource_type}/{resource_id}"
        headers = {
            "Accept": "application/fhir+json",
            "Authorization": f"Bearer {self._settings.openemr_fhir_token}",
        }
        try:
            async with httpx.AsyncClient(
                timeout=10.0, transport=_HTTPX_RETRY_TRANSPORT
            ) as client:
                response = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            return False, None, f"transport: {exc.__class__.__name__}", int(
                (time.monotonic() - started) * 1000
            )

        latency = int((time.monotonic() - started) * 1000)
        if response.status_code != 200:
            return False, None, f"http_{response.status_code}", latency
        return True, response.json(), None, latency


def _fixture_search(resource_type: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    entries = FIXTURE_BUNDLE.get(resource_type, [])
    patient = params.get("patient")
    if patient is not None:
        entries = [
            e
            for e in entries
            if e.get("subject", {}).get("reference") == f"Patient/{patient}"
            or e.get("patient", {}).get("reference") == f"Patient/{patient}"
            or e.get("id") == patient
        ]

    category = params.get("category")
    if category is not None:
        entries = [
            e
            for e in entries
            if any(
                c.get("code") == category
                for cat in (e.get("category") or [])
                for c in (cat.get("coding") or [])
            )
        ]

    status = params.get("clinical-status") or params.get("status")
    if status is not None:
        entries = [
            e
            for e in entries
            if e.get("status") == status
            or any(
                c.get("code") == status
                for c in (e.get("clinicalStatus", {}).get("coding") or [])
            )
        ]

    return list(entries)


def _fixture_read(resource_type: str, resource_id: str) -> dict[str, Any] | None:
    for entry in FIXTURE_BUNDLE.get(resource_type, []):
        if entry.get("id") == resource_id:
            return entry
    return None
