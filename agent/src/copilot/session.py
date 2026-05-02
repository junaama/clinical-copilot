"""Standalone session management for the Clinical Co-Pilot.

Manages OAuth launch state, user sessions (HttpOnly cookie-backed), and token
bundles for the SMART-on-FHIR standalone launch flow.  The EHR-launch flow
(``smart.py``) is unchanged and continues to work in parallel.

Two storage backends:

- **InMemorySessionStore** — zero-setup, single-process.  Used by tests and
  dev when ``CHECKPOINTER_DSN`` is unset.
- **PostgresSessionStore** (future) — durable, multi-replica.  Created when a
  DSN is available.  Tables are created idempotently via ``ensure_schema()``.

The ``SessionGateway`` facade delegates to whichever store is injected at
construction.  Callers never import the store classes directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

# ---------------------------------------------------------------------------
# Data rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LaunchStateRow:
    """Short-lived PKCE row stashed during /auth/login, popped during callback."""

    state: str
    code_verifier: str
    redirect_uri: str
    expires_at: float


@dataclass(frozen=True)
class SessionRow:
    """Represents an authenticated user session."""

    session_id: str
    oe_user_id: int
    display_name: str
    fhir_user: str  # e.g. "Practitioner/abc-123-uuid"
    created_at: float
    expires_at: float


@dataclass(frozen=True)
class TokenBundleRow:
    """Access/refresh/id tokens associated with a session."""

    session_id: str
    access_token: str
    refresh_token: str
    id_token: str
    scope: str
    issuer: str
    expires_at: float


# ---------------------------------------------------------------------------
# fhirUser claim parsing
# ---------------------------------------------------------------------------


def parse_fhir_user(fhir_user: str) -> tuple[str, str]:
    """Parse a ``fhirUser`` claim into ``(resource_type, uuid)``.

    Accepts both relative (``Practitioner/abc-123``) and absolute
    (``https://…/fhir/Practitioner/abc-123``) references.

    Returns ``("", "")`` for empty or unparseable input.
    """
    if not fhir_user:
        return ("", "")
    # Strip any base URL prefix — take the last two path segments.
    parts = fhir_user.rstrip("/").split("/")
    if len(parts) >= 2:
        return (parts[-2], parts[-1])
    return ("", "")


# ---------------------------------------------------------------------------
# Store protocol
# ---------------------------------------------------------------------------


class SessionStore(Protocol):
    """Abstract storage backend for session data."""

    async def put_launch_state(self, row: LaunchStateRow) -> None: ...
    async def pop_launch_state(self, state: str) -> LaunchStateRow | None: ...
    async def put_session(self, row: SessionRow) -> None: ...
    async def get_session(self, session_id: str) -> SessionRow | None: ...
    async def delete_session(self, session_id: str) -> None: ...
    async def put_token_bundle(self, row: TokenBundleRow) -> None: ...
    async def get_token_bundle(self, session_id: str) -> TokenBundleRow | None: ...


# ---------------------------------------------------------------------------
# In-memory store (tests / dev)
# ---------------------------------------------------------------------------


class InMemorySessionStore:
    """Dict-backed store.  Process-local, single-replica only."""

    def __init__(self) -> None:
        self._launch_states: dict[str, LaunchStateRow] = {}
        self._sessions: dict[str, SessionRow] = {}
        self._token_bundles: dict[str, TokenBundleRow] = {}

    async def put_launch_state(self, row: LaunchStateRow) -> None:
        self._launch_states[row.state] = row

    async def pop_launch_state(self, state: str) -> LaunchStateRow | None:
        row = self._launch_states.pop(state, None)
        if row is None:
            return None
        if row.expires_at < time.time():
            return None  # expired
        return row

    async def put_session(self, row: SessionRow) -> None:
        self._sessions[row.session_id] = row

    async def get_session(self, session_id: str) -> SessionRow | None:
        row = self._sessions.get(session_id)
        if row is None:
            return None
        if row.expires_at < time.time():
            # Lazy eviction on read.
            self._sessions.pop(session_id, None)
            return None
        return row

    async def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    async def put_token_bundle(self, row: TokenBundleRow) -> None:
        self._token_bundles[row.session_id] = row

    async def get_token_bundle(self, session_id: str) -> TokenBundleRow | None:
        return self._token_bundles.get(session_id)


# ---------------------------------------------------------------------------
# Gateway facade
# ---------------------------------------------------------------------------


class SessionGateway:
    """Facade over a ``SessionStore`` backend.

    Provides the public API consumed by the FastAPI endpoints.  Callers inject
    the store backend at construction; the gateway is backend-agnostic.
    """

    def __init__(self, store: SessionStore) -> None:
        self._store = store

    # -- Launch state --

    async def create_launch_state(self, row: LaunchStateRow) -> None:
        await self._store.put_launch_state(row)

    async def pop_launch_state(self, state: str) -> LaunchStateRow | None:
        return await self._store.pop_launch_state(state)

    # -- Sessions --

    async def create_session(self, row: SessionRow) -> None:
        await self._store.put_session(row)

    async def get_session(self, session_id: str) -> SessionRow | None:
        return await self._store.get_session(session_id)

    async def delete_session(self, session_id: str) -> None:
        await self._store.delete_session(session_id)

    # -- Token bundles --

    async def upsert_token_bundle(self, row: TokenBundleRow) -> None:
        await self._store.put_token_bundle(row)

    async def get_token_bundle(self, session_id: str) -> TokenBundleRow | None:
        return await self._store.get_token_bundle(session_id)
