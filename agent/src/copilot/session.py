"""Standalone session management for the Clinical Co-Pilot.

Manages OAuth launch state, user sessions (HttpOnly cookie-backed), and token
bundles for the SMART-on-FHIR standalone launch flow.  The EHR-launch flow
(``smart.py``) is unchanged and continues to work in parallel.

Two storage backends:

- **InMemorySessionStore** — zero-setup, single-process.  Used by tests and
  dev when ``CHECKPOINTER_DSN`` is unset.
- **PostgresSessionStore** — durable, multi-replica.  Created when a DSN is
  available.  Tables are created idempotently via ``ensure_schema()``.

The ``SessionGateway`` facade delegates to whichever store is injected at
construction.  Callers never import the store classes directly — use the
``open_session_store(dsn)`` async context manager for the Postgres backend
or instantiate ``InMemorySessionStore`` directly for tests.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

# Refresh tokens this many seconds before their nominal expiry so an in-flight
# request whose first leg uses the bundle doesn't race a server that's already
# rotated the access token internally.  Mirrors smart.py's TOKEN_TTL_SLACK_SECONDS.
DEFAULT_REFRESH_SKEW_SECONDS = 30

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
# Postgres store (production)
# ---------------------------------------------------------------------------


_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS copilot_oauth_launch_state (
    state TEXT PRIMARY KEY,
    code_verifier TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS copilot_session (
    session_id TEXT PRIMARY KEY,
    oe_user_id INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    fhir_user TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS copilot_token_bundle (
    session_id TEXT PRIMARY KEY,
    access_token TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    id_token TEXT NOT NULL,
    scope TEXT NOT NULL,
    issuer TEXT NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL
);
"""


class PostgresSessionStore:
    """Postgres-backed implementation of ``SessionStore``.

    Owns an ``AsyncConnectionPool`` for the lifetime of the store. Use via
    ``open_session_store(dsn)`` rather than constructing directly so the pool
    is opened and closed cleanly.

    Token columns are stored as plaintext today; encryption-at-rest is
    finalized in ``issues/009-token-encryption-at-rest.md``.
    """

    def __init__(self, pool: object) -> None:
        # Typed as ``object`` to avoid a hard import of psycopg_pool at module
        # import time (the postgres extra is optional). The pool is duck-typed
        # against ``AsyncConnectionPool``.
        self._pool = pool

    async def ensure_schema(self) -> None:
        """Create the three session tables idempotently. Safe to call repeatedly."""
        async with self._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(_SCHEMA_DDL)

    # -- Launch state --

    async def put_launch_state(self, row: LaunchStateRow) -> None:
        async with self._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO copilot_oauth_launch_state
                        (state, code_verifier, redirect_uri, expires_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (state) DO UPDATE SET
                        code_verifier = EXCLUDED.code_verifier,
                        redirect_uri = EXCLUDED.redirect_uri,
                        expires_at = EXCLUDED.expires_at
                    """,
                    (row.state, row.code_verifier, row.redirect_uri, row.expires_at),
                )

    async def pop_launch_state(self, state: str) -> LaunchStateRow | None:
        # Single-shot DELETE … RETURNING handles the replay-rejection
        # invariant: the row is gone after the first successful pop.
        async with self._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    DELETE FROM copilot_oauth_launch_state
                    WHERE state = %s
                    RETURNING state, code_verifier, redirect_uri, expires_at
                    """,
                    (state,),
                )
                fetched = await cur.fetchone()
        if fetched is None:
            return None
        row = LaunchStateRow(
            state=fetched[0],
            code_verifier=fetched[1],
            redirect_uri=fetched[2],
            expires_at=float(fetched[3]),
        )
        if row.expires_at < time.time():
            return None
        return row

    # -- Sessions --

    async def put_session(self, row: SessionRow) -> None:
        async with self._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO copilot_session
                        (session_id, oe_user_id, display_name, fhir_user,
                         created_at, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (session_id) DO UPDATE SET
                        oe_user_id = EXCLUDED.oe_user_id,
                        display_name = EXCLUDED.display_name,
                        fhir_user = EXCLUDED.fhir_user,
                        created_at = EXCLUDED.created_at,
                        expires_at = EXCLUDED.expires_at
                    """,
                    (
                        row.session_id,
                        row.oe_user_id,
                        row.display_name,
                        row.fhir_user,
                        row.created_at,
                        row.expires_at,
                    ),
                )

    async def get_session(self, session_id: str) -> SessionRow | None:
        async with self._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT session_id, oe_user_id, display_name, fhir_user,
                           created_at, expires_at
                    FROM copilot_session
                    WHERE session_id = %s
                    """,
                    (session_id,),
                )
                fetched = await cur.fetchone()
        if fetched is None:
            return None
        row = SessionRow(
            session_id=fetched[0],
            oe_user_id=int(fetched[1]),
            display_name=fetched[2],
            fhir_user=fetched[3],
            created_at=float(fetched[4]),
            expires_at=float(fetched[5]),
        )
        if row.expires_at < time.time():
            await self.delete_session(session_id)
            return None
        return row

    async def delete_session(self, session_id: str) -> None:
        async with self._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                # Token bundle is dropped first to avoid orphan rows. Same
                # transaction so a /logout never leaves tokens behind.
                await cur.execute(
                    "DELETE FROM copilot_token_bundle WHERE session_id = %s",
                    (session_id,),
                )
                await cur.execute(
                    "DELETE FROM copilot_session WHERE session_id = %s",
                    (session_id,),
                )

    # -- Token bundles --

    async def put_token_bundle(self, row: TokenBundleRow) -> None:
        async with self._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO copilot_token_bundle
                        (session_id, access_token, refresh_token, id_token,
                         scope, issuer, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (session_id) DO UPDATE SET
                        access_token = EXCLUDED.access_token,
                        refresh_token = EXCLUDED.refresh_token,
                        id_token = EXCLUDED.id_token,
                        scope = EXCLUDED.scope,
                        issuer = EXCLUDED.issuer,
                        expires_at = EXCLUDED.expires_at
                    """,
                    (
                        row.session_id,
                        row.access_token,
                        row.refresh_token,
                        row.id_token,
                        row.scope,
                        row.issuer,
                        row.expires_at,
                    ),
                )

    async def get_token_bundle(self, session_id: str) -> TokenBundleRow | None:
        async with self._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT session_id, access_token, refresh_token, id_token,
                           scope, issuer, expires_at
                    FROM copilot_token_bundle
                    WHERE session_id = %s
                    """,
                    (session_id,),
                )
                fetched = await cur.fetchone()
        if fetched is None:
            return None
        return TokenBundleRow(
            session_id=fetched[0],
            access_token=fetched[1],
            refresh_token=fetched[2],
            id_token=fetched[3],
            scope=fetched[4],
            issuer=fetched[5],
            expires_at=float(fetched[6]),
        )


@asynccontextmanager
async def open_session_store(dsn: str) -> AsyncIterator[PostgresSessionStore]:
    """Open a Postgres-backed session store for the lifetime of the block.

    Opens an ``AsyncConnectionPool``, runs ``ensure_schema()`` so callers can
    use the store immediately, and closes the pool on exit.

    Requires the ``postgres`` extra: ``uv sync --extra postgres``.
    """
    try:
        from psycopg_pool import AsyncConnectionPool
    except ImportError as exc:
        raise RuntimeError(
            "open_session_store requires the 'postgres' extra. "
            "Install with: uv sync --extra postgres"
        ) from exc

    pool = AsyncConnectionPool(dsn, open=False, min_size=1, max_size=4)
    await pool.open()
    try:
        store = PostgresSessionStore(pool)
        await store.ensure_schema()
        yield store
    finally:
        await pool.close()


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

    async def get_fresh_token_bundle(
        self,
        session_id: str,
        *,
        refresh_fn: Callable[[str], Awaitable[dict[str, Any]]],
        skew_seconds: int = DEFAULT_REFRESH_SKEW_SECONDS,
        now: float | None = None,
    ) -> TokenBundleRow | None:
        """Return a non-expired token bundle for ``session_id``, refreshing if needed.

        - Returns ``None`` if no bundle exists (caller forces re-login).
        - If ``expires_at`` is more than ``skew_seconds`` away, returns the
          stored bundle as-is (the hot path).
        - Otherwise calls ``refresh_fn(refresh_token)`` to mint a new access
          token, persists the rotated bundle, and returns it.

        ``refresh_fn`` is the seam: callers inject the actual httpx call
        (``smart.refresh_access_token``) so the gateway stays unaware of
        OAuth client credentials and the token endpoint.

        Refresh failures (``RuntimeError`` from the helper) propagate. The
        stale bundle is **not** evicted on failure — the caller decides
        whether to clear it or surface a "please log in again" UX.
        """
        bundle = await self._store.get_token_bundle(session_id)
        if bundle is None:
            return None

        clock = now if now is not None else time.time()
        if bundle.expires_at - clock > skew_seconds:
            return bundle

        payload = await refresh_fn(bundle.refresh_token)
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise RuntimeError(
                "refresh_fn returned no access_token; cannot rotate bundle"
            )

        # Some token endpoints don't rotate the refresh token. Carry the old
        # one forward so the next refresh still has a credential to present.
        new_refresh = str(payload.get("refresh_token") or "") or bundle.refresh_token
        new_id_token = str(payload.get("id_token") or "") or bundle.id_token
        new_scope = str(payload.get("scope") or "") or bundle.scope
        expires_in = int(payload.get("expires_in") or 3600)

        rotated = TokenBundleRow(
            session_id=session_id,
            access_token=access_token,
            refresh_token=new_refresh,
            id_token=new_id_token,
            scope=new_scope,
            issuer=bundle.issuer,
            expires_at=clock + expires_in,
        )
        await self._store.put_token_bundle(rotated)
        return rotated
