"""Conversation sidebar metadata.

Stores the user-facing thread row (id = LangGraph thread_id, title,
last_focus_pid, timestamps). The LangGraph checkpointer remains the source
of truth for message history and ``CoPilotState``; this module only owns
sidebar metadata and the per-turn ``updated_at`` / ``last_focus_pid`` write.

Two storage backends mirror ``session.py``:

- **InMemoryConversationStore** — used by tests and dev (no DSN).
- **PostgresConversationStore** — durable, multi-replica.

The ``ConversationRegistry`` facade delegates to the injected store. Issue
008 will plug Haiku title-summarization into ``set_title``; this slice
ships truncated-first-message titles.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Protocol

# Sidebar layout breaks past ~60 chars on common widths. The PRD specifies
# this ceiling; ``derive_title_from_message`` is the canonical truncator and
# ``set_title`` enforces the same limit defensively for any caller that
# hand-builds a title.
TITLE_MAX_CHARS = 60

# Fallback title for a turn whose message is empty/whitespace. Keeps the
# sidebar row from rendering blank.
_EMPTY_TITLE_FALLBACK = "(untitled)"


@dataclass(frozen=True)
class ConversationRow:
    """One sidebar row.

    ``id`` doubles as the LangGraph thread_id so resuming a thread is just a
    matter of passing this id into ``/chat``'s ``conversation_id`` field —
    the checkpointer rehydrates ``CoPilotState`` from there.
    """

    id: str
    user_id: str
    title: str
    last_focus_pid: str
    created_at: float
    updated_at: float
    archived_at: float | None = None


def derive_title_from_message(message: str) -> str:
    """Truncate the first user message into a sidebar-shaped title.

    - Strip leading/trailing whitespace.
    - Collapse internal newlines (sidebar is single-line per row).
    - Truncate to ``TITLE_MAX_CHARS``.
    - Empty / whitespace-only input falls back to a generic placeholder so
      no row ever renders blank.

    Issue 008 swaps this for a Haiku summary on a separate write-behind
    pass; until that ships this is the title users see.
    """
    if not message:
        return _EMPTY_TITLE_FALLBACK
    collapsed = " ".join(message.split())
    if not collapsed:
        return _EMPTY_TITLE_FALLBACK
    if len(collapsed) <= TITLE_MAX_CHARS:
        return collapsed
    return collapsed[:TITLE_MAX_CHARS]


# ---------------------------------------------------------------------------
# Store protocol
# ---------------------------------------------------------------------------


class ConversationStore(Protocol):
    """Abstract storage backend for conversation rows."""

    async def create(self, row: ConversationRow) -> None: ...
    async def get(self, conversation_id: str) -> ConversationRow | None: ...
    async def list_for_user(self, user_id: str) -> list[ConversationRow]: ...
    async def update(self, row: ConversationRow) -> None: ...


# ---------------------------------------------------------------------------
# In-memory store (tests / dev)
# ---------------------------------------------------------------------------


class InMemoryConversationStore:
    """Dict-backed store. Process-local, single-replica only."""

    def __init__(self) -> None:
        self._rows: dict[str, ConversationRow] = {}

    async def create(self, row: ConversationRow) -> None:
        self._rows[row.id] = row

    async def get(self, conversation_id: str) -> ConversationRow | None:
        return self._rows.get(conversation_id)

    async def list_for_user(self, user_id: str) -> list[ConversationRow]:
        rows = [
            r
            for r in self._rows.values()
            if r.user_id == user_id and r.archived_at is None
        ]
        rows.sort(key=lambda r: r.updated_at, reverse=True)
        return rows

    async def update(self, row: ConversationRow) -> None:
        if row.id in self._rows:
            self._rows[row.id] = row


# ---------------------------------------------------------------------------
# Postgres store (production)
# ---------------------------------------------------------------------------


_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS copilot_conversation (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    last_focus_pid TEXT NOT NULL DEFAULT '',
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL,
    archived_at DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS copilot_conversation_user_active_updated_idx
    ON copilot_conversation (user_id, updated_at DESC)
    WHERE archived_at IS NULL;
"""


class PostgresConversationStore:
    """Postgres-backed implementation of ``ConversationStore``.

    Owns an ``AsyncConnectionPool`` for the lifetime of the store. Use via
    ``open_conversation_store(dsn)``.
    """

    def __init__(self, pool: object) -> None:
        # Typed as ``object`` to avoid a hard import of psycopg_pool at
        # module-load time (the postgres extra is optional).
        self._pool = pool

    async def ensure_schema(self) -> None:
        """Create the conversation table + index idempotently."""
        async with self._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(_SCHEMA_DDL)

    async def create(self, row: ConversationRow) -> None:
        async with self._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO copilot_conversation
                        (id, user_id, title, last_focus_pid,
                         created_at, updated_at, archived_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        row.id,
                        row.user_id,
                        row.title,
                        row.last_focus_pid,
                        row.created_at,
                        row.updated_at,
                        row.archived_at,
                    ),
                )

    async def get(self, conversation_id: str) -> ConversationRow | None:
        async with self._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, user_id, title, last_focus_pid,
                           created_at, updated_at, archived_at
                    FROM copilot_conversation
                    WHERE id = %s
                    """,
                    (conversation_id,),
                )
                fetched = await cur.fetchone()
        if fetched is None:
            return None
        return ConversationRow(
            id=fetched[0],
            user_id=fetched[1],
            title=fetched[2],
            last_focus_pid=fetched[3] or "",
            created_at=float(fetched[4]),
            updated_at=float(fetched[5]),
            archived_at=float(fetched[6]) if fetched[6] is not None else None,
        )

    async def list_for_user(self, user_id: str) -> list[ConversationRow]:
        async with self._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, user_id, title, last_focus_pid,
                           created_at, updated_at, archived_at
                    FROM copilot_conversation
                    WHERE user_id = %s AND archived_at IS NULL
                    ORDER BY updated_at DESC
                    """,
                    (user_id,),
                )
                fetched = await cur.fetchall()
        return [
            ConversationRow(
                id=r[0],
                user_id=r[1],
                title=r[2],
                last_focus_pid=r[3] or "",
                created_at=float(r[4]),
                updated_at=float(r[5]),
                archived_at=float(r[6]) if r[6] is not None else None,
            )
            for r in fetched
        ]

    async def update(self, row: ConversationRow) -> None:
        async with self._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE copilot_conversation
                    SET title = %s,
                        last_focus_pid = %s,
                        updated_at = %s,
                        archived_at = %s
                    WHERE id = %s
                    """,
                    (
                        row.title,
                        row.last_focus_pid,
                        row.updated_at,
                        row.archived_at,
                        row.id,
                    ),
                )


@asynccontextmanager
async def open_conversation_store(
    dsn: str,
) -> AsyncIterator[PostgresConversationStore]:
    """Open a Postgres-backed conversation store for the lifetime of the block.

    Mirrors ``open_session_store`` — opens a connection pool, runs
    ``ensure_schema()``, yields the store, closes the pool on exit.
    """
    try:
        from psycopg_pool import AsyncConnectionPool
    except ImportError as exc:
        raise RuntimeError(
            "open_conversation_store requires the 'postgres' extra. "
            "Install with: uv sync --extra postgres"
        ) from exc

    pool = AsyncConnectionPool(dsn, open=False, min_size=1, max_size=4)
    await pool.open()
    try:
        store = PostgresConversationStore(pool)
        await store.ensure_schema()
        yield store
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Registry facade
# ---------------------------------------------------------------------------


class ConversationRegistry:
    """Public API consumed by the FastAPI conversation endpoints and the
    /chat write-behind that touches ``updated_at`` after each turn.

    All write methods are no-ops on unknown ids — the chat path may legitimately
    fire ``touch`` / ``ensure_first_turn_title`` against a conversation_id that
    hasn't been registered yet (the very first turn of a brand-new thread that
    skipped the explicit ``POST /conversations`` create call).
    """

    def __init__(self, store: ConversationStore) -> None:
        self._store = store

    async def create(
        self,
        *,
        conversation_id: str,
        user_id: str,
        title: str = "",
    ) -> ConversationRow:
        now = time.time()
        row = ConversationRow(
            id=conversation_id,
            user_id=user_id,
            title=title,
            last_focus_pid="",
            created_at=now,
            updated_at=now,
            archived_at=None,
        )
        await self._store.create(row)
        return row

    async def get(self, conversation_id: str) -> ConversationRow | None:
        return await self._store.get(conversation_id)

    async def list_for_user(self, user_id: str) -> list[ConversationRow]:
        return await self._store.list_for_user(user_id)

    async def touch(self, conversation_id: str, *, focus_pid: str) -> None:
        """Advance ``updated_at`` to now and persist ``last_focus_pid``.

        Empty ``focus_pid`` preserves the existing pid — a turn that didn't
        change focus shouldn't blank the row's last-known patient.
        """
        existing = await self._store.get(conversation_id)
        if existing is None:
            return
        new_pid = focus_pid or existing.last_focus_pid
        # Monotonic guard: ``time.time()`` can return the same value on
        # back-to-back calls on coarse-clock systems; nudge forward by a
        # microsecond so list ordering remains stable.
        now = max(time.time(), existing.updated_at + 1e-6)
        updated = replace(existing, last_focus_pid=new_pid, updated_at=now)
        await self._store.update(updated)

    async def set_title(self, conversation_id: str, title: str) -> None:
        existing = await self._store.get(conversation_id)
        if existing is None:
            return
        truncated = title[:TITLE_MAX_CHARS] if title else ""
        updated = replace(existing, title=truncated)
        await self._store.update(updated)

    async def ensure_first_turn_title(
        self, conversation_id: str, message: str
    ) -> bool:
        """Set the title from the first user message — only if blank.

        Returns ``True`` when a write happened, ``False`` otherwise. The
        write is a no-op when (a) the row doesn't exist, or (b) the title
        is already populated. Idempotency is what makes the chat-path
        write-behind safe to call on every turn rather than just the first.
        """
        existing = await self._store.get(conversation_id)
        if existing is None:
            return False
        if existing.title:
            return False
        new_title = derive_title_from_message(message)
        updated = replace(existing, title=new_title)
        await self._store.update(updated)
        return True

    async def archive(self, conversation_id: str) -> None:
        """Mark the row archived. Archive UI is deferred (issue 004 leaves
        the column unexposed); kept here so ``list_for_user`` can be
        verified against an archived fixture in tests."""
        existing = await self._store.get(conversation_id)
        if existing is None:
            return
        updated = replace(existing, archived_at=time.time())
        await self._store.update(updated)
