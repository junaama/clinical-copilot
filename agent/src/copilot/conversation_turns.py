"""Structured per-turn provenance store (issue 045).

Sidebar reopen rehydrates a conversation by replaying each turn pair. The
LangGraph checkpointer holds the message history but only the most-recent
turn's structured ``block`` (the ``CoPilotState.block`` field is overwritten
on every turn), so we cannot reach back through the checkpoint to recover
prior turns' route metadata, citation chips, or block kind. This module owns
the per-turn snapshot — one row per assistant turn — so reopen can restore
the same surface the clinician saw the first time.

Two storage backends mirror ``conversations.py`` and ``session.py``:

- **InMemoryTurnStore** — used by tests and dev (no DSN).
- **PostgresTurnStore** — durable, multi-replica.

The ``ConversationTurnRegistry`` facade delegates to the injected store. The
chat handler appends one turn after each /chat completes; the messages
endpoint reads them in turn-index order. Legacy conversations with no
stored turns fall back to the LangGraph checkpoint scan, which renders as
plain text — that path is preserved for backward compatibility.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ConversationTurn:
    """One assistant turn's structured provenance.

    ``turn_index`` is monotonic per conversation, starting at 0. ``block``
    carries the structured wire shape (already validated by the chat
    handler). ``route_kind`` / ``route_label`` mirror ``ChatResponse.state.route``.
    Diagnostics fields mirror ``ChatResponse.state.diagnostics`` so the UI's
    Technical-details affordance survives rehydration.
    """

    conversation_id: str
    turn_index: int
    user_message: str
    assistant_text: str
    block: dict[str, Any]
    route_kind: str
    route_label: str
    workflow_id: str = ""
    classifier_confidence: float = 0.0
    decision: str = ""
    supervisor_action: str = ""
    created_at: float = field(default_factory=time.time)


class TurnStore(Protocol):
    """Abstract storage backend for ``ConversationTurn`` rows."""

    async def append(self, turn: ConversationTurn) -> None: ...
    async def list_for_conversation(
        self, conversation_id: str
    ) -> list[ConversationTurn]: ...
    async def next_turn_index(self, conversation_id: str) -> int: ...


# ---------------------------------------------------------------------------
# In-memory store (tests / dev)
# ---------------------------------------------------------------------------


class InMemoryTurnStore:
    """Dict-backed store. Process-local, single-replica only."""

    def __init__(self) -> None:
        self._rows: dict[str, list[ConversationTurn]] = {}

    async def append(self, turn: ConversationTurn) -> None:
        bucket = self._rows.setdefault(turn.conversation_id, [])
        bucket.append(turn)

    async def list_for_conversation(
        self, conversation_id: str
    ) -> list[ConversationTurn]:
        bucket = list(self._rows.get(conversation_id, ()))
        bucket.sort(key=lambda t: t.turn_index)
        return bucket

    async def next_turn_index(self, conversation_id: str) -> int:
        bucket = self._rows.get(conversation_id, ())
        if not bucket:
            return 0
        return max(t.turn_index for t in bucket) + 1


# ---------------------------------------------------------------------------
# Postgres store (production)
# ---------------------------------------------------------------------------


_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS copilot_conversation_turn (
    conversation_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    user_message TEXT NOT NULL,
    assistant_text TEXT NOT NULL,
    block_json TEXT NOT NULL,
    route_kind TEXT NOT NULL,
    route_label TEXT NOT NULL,
    workflow_id TEXT NOT NULL DEFAULT '',
    classifier_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    decision TEXT NOT NULL DEFAULT '',
    supervisor_action TEXT NOT NULL DEFAULT '',
    created_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (conversation_id, turn_index)
);

CREATE INDEX IF NOT EXISTS copilot_conversation_turn_conv_idx
    ON copilot_conversation_turn (conversation_id, turn_index);
"""


class PostgresTurnStore:
    """Postgres-backed implementation of ``TurnStore``.

    Owns an ``AsyncConnectionPool`` for the lifetime of the store. Use via
    ``open_conversation_turn_store(dsn)``.
    """

    def __init__(self, pool: object) -> None:
        # Typed as ``object`` to avoid a hard import of psycopg_pool at
        # module-load time (the postgres extra is optional).
        self._pool = pool

    async def ensure_schema(self) -> None:
        async with self._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(_SCHEMA_DDL)

    async def append(self, turn: ConversationTurn) -> None:
        async with self._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO copilot_conversation_turn
                        (conversation_id, turn_index, user_message,
                         assistant_text, block_json, route_kind, route_label,
                         workflow_id, classifier_confidence, decision,
                         supervisor_action, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (conversation_id, turn_index) DO NOTHING
                    """,
                    (
                        turn.conversation_id,
                        turn.turn_index,
                        turn.user_message,
                        turn.assistant_text,
                        json.dumps(turn.block),
                        turn.route_kind,
                        turn.route_label,
                        turn.workflow_id,
                        turn.classifier_confidence,
                        turn.decision,
                        turn.supervisor_action,
                        turn.created_at,
                    ),
                )

    async def list_for_conversation(
        self, conversation_id: str
    ) -> list[ConversationTurn]:
        async with self._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT conversation_id, turn_index, user_message,
                           assistant_text, block_json, route_kind, route_label,
                           workflow_id, classifier_confidence, decision,
                           supervisor_action, created_at
                    FROM copilot_conversation_turn
                    WHERE conversation_id = %s
                    ORDER BY turn_index
                    """,
                    (conversation_id,),
                )
                fetched = await cur.fetchall()
        return [
            ConversationTurn(
                conversation_id=r[0],
                turn_index=int(r[1]),
                user_message=r[2],
                assistant_text=r[3],
                block=json.loads(r[4]) if r[4] else {},
                route_kind=r[5],
                route_label=r[6],
                workflow_id=r[7] or "",
                classifier_confidence=float(r[8] or 0.0),
                decision=r[9] or "",
                supervisor_action=r[10] or "",
                created_at=float(r[11]),
            )
            for r in fetched
        ]

    async def next_turn_index(self, conversation_id: str) -> int:
        async with self._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT COALESCE(MAX(turn_index), -1) + 1
                    FROM copilot_conversation_turn
                    WHERE conversation_id = %s
                    """,
                    (conversation_id,),
                )
                row = await cur.fetchone()
        return int(row[0]) if row is not None else 0


@asynccontextmanager
async def open_conversation_turn_store(
    dsn: str,
) -> AsyncIterator[PostgresTurnStore]:
    """Open a Postgres-backed turn store for the lifetime of the block.

    Mirrors ``open_conversation_store`` — opens a connection pool, runs
    ``ensure_schema()``, yields the store, closes the pool on exit.
    """
    try:
        from psycopg_pool import AsyncConnectionPool
    except ImportError as exc:
        raise RuntimeError(
            "open_conversation_turn_store requires the 'postgres' extra. "
            "Install with: uv sync --extra postgres"
        ) from exc

    pool = AsyncConnectionPool(dsn, open=False, min_size=1, max_size=4)
    await pool.open()
    try:
        store = PostgresTurnStore(pool)
        await store.ensure_schema()
        yield store
    finally:
        await pool.close()


# ---------------------------------------------------------------------------
# Registry facade
# ---------------------------------------------------------------------------


class ConversationTurnRegistry:
    """Public API consumed by the chat handler and the messages endpoint.

    ``append_turn`` allocates a fresh ``turn_index`` per conversation and
    persists the row. ``list_turns`` returns them in chronological order.
    """

    def __init__(self, store: TurnStore) -> None:
        self._store = store

    async def append_turn(
        self,
        *,
        conversation_id: str,
        user_message: str,
        assistant_text: str,
        block: dict[str, Any],
        route_kind: str,
        route_label: str,
        workflow_id: str = "",
        classifier_confidence: float = 0.0,
        decision: str = "",
        supervisor_action: str = "",
    ) -> ConversationTurn:
        next_index = await self._store.next_turn_index(conversation_id)
        turn = ConversationTurn(
            conversation_id=conversation_id,
            turn_index=next_index,
            user_message=user_message,
            assistant_text=assistant_text,
            block=block,
            route_kind=route_kind,
            route_label=route_label,
            workflow_id=workflow_id,
            classifier_confidence=classifier_confidence,
            decision=decision,
            supervisor_action=supervisor_action,
        )
        await self._store.append(turn)
        return turn

    async def list_turns(self, conversation_id: str) -> list[ConversationTurn]:
        return await self._store.list_for_conversation(conversation_id)
