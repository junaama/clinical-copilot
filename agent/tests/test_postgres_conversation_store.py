"""Postgres-backed ConversationStore integration test.

Skipped when ``COPILOT_TEST_PG_DSN`` isn't set. Mirrors
``test_postgres_session_store.py`` so a single Postgres instance covers
checkpointer, sessions, and conversations.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from copilot.conversations import (
    ConversationRow,
    open_conversation_store,
)

_DSN = os.environ.get("COPILOT_TEST_PG_DSN", "")

pytestmark = pytest.mark.skipif(
    not _DSN, reason="set COPILOT_TEST_PG_DSN to run this integration test"
)


def _row_id() -> str:
    return uuid.uuid4().hex[:12]


def _row(
    *,
    conversation_id: str,
    user_id: str,
    title: str = "",
    last_focus_pid: str = "",
    archived_at: float | None = None,
) -> ConversationRow:
    now = time.time()
    return ConversationRow(
        id=conversation_id,
        user_id=user_id,
        title=title,
        last_focus_pid=last_focus_pid,
        created_at=now,
        updated_at=now,
        archived_at=archived_at,
    )


async def test_ensure_schema_is_idempotent() -> None:
    async with open_conversation_store(_DSN) as store:
        await store.ensure_schema()
        await store.ensure_schema()


async def test_create_and_get_round_trip() -> None:
    async with open_conversation_store(_DSN) as store:
        cid = f"conv-{_row_id()}"
        await store.create(_row(conversation_id=cid, user_id="u-1", title="hi"))
        got = await store.get(cid)
        assert got is not None
        assert got.title == "hi"
        assert got.user_id == "u-1"


async def test_create_is_idempotent_on_conflict() -> None:
    """``create`` is ON CONFLICT DO NOTHING — a second call must not blow up
    or overwrite the row, mirroring the in-memory store's first-write-wins
    semantics."""
    async with open_conversation_store(_DSN) as store:
        cid = f"conv-conflict-{_row_id()}"
        await store.create(_row(conversation_id=cid, user_id="u", title="first"))
        await store.create(_row(conversation_id=cid, user_id="u", title="second"))
        got = await store.get(cid)
        assert got is not None
        assert got.title == "first"


async def test_list_for_user_orders_desc_excludes_archived() -> None:
    import asyncio

    user = f"u-{_row_id()}"
    async with open_conversation_store(_DSN) as store:
        old_id = f"conv-old-{_row_id()}"
        new_id = f"conv-new-{_row_id()}"
        archived_id = f"conv-arch-{_row_id()}"

        await store.create(_row(conversation_id=old_id, user_id=user, title="old"))
        await asyncio.sleep(0.01)
        await store.create(_row(conversation_id=new_id, user_id=user, title="new"))
        archived_row = _row(
            conversation_id=archived_id,
            user_id=user,
            title="archived",
            archived_at=time.time(),
        )
        await store.create(archived_row)

        rows = await store.list_for_user(user)
        ids = [r.id for r in rows]
        assert ids == [new_id, old_id]


async def test_update_persists_focus_and_updated_at() -> None:
    async with open_conversation_store(_DSN) as store:
        cid = f"conv-upd-{_row_id()}"
        original = _row(conversation_id=cid, user_id="u", title="t")
        await store.create(original)

        from dataclasses import replace

        bumped = replace(
            original,
            last_focus_pid="fixture-1",
            updated_at=original.updated_at + 5,
        )
        await store.update(bumped)

        got = await store.get(cid)
        assert got is not None
        assert got.last_focus_pid == "fixture-1"
        assert got.updated_at == original.updated_at + 5


async def test_state_survives_across_store_instances() -> None:
    """Process restart must not erase a user's sidebar."""
    cid = f"conv-durable-{_row_id()}"
    async with open_conversation_store(_DSN) as store:
        await store.create(
            _row(conversation_id=cid, user_id="u-durable", title="persistent")
        )

    async with open_conversation_store(_DSN) as store2:
        got = await store2.get(cid)
        assert got is not None
        assert got.title == "persistent"
