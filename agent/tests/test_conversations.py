"""ConversationRegistry — sidebar metadata for the multi-conversation shell.

Exercises the registry through its public interface using the in-memory
backend so tests run without Postgres. Covers:

- ``create`` returns a row with the supplied id and the current user as owner
- ``list_for_user`` is scoped to user, ordered by ``updated_at DESC``,
  excludes archived rows
- ``get`` returns the full row (or None for unknown id)
- ``touch`` advances ``updated_at`` and persists ``last_focus_pid``
- ``set_title`` overwrites the placeholder title
- ``derive_title_from_message`` truncates to ≤60 chars and strips whitespace

The Postgres backend mirrors the in-memory contract; integration is gated
behind ``COPILOT_TEST_PG_DSN`` (see ``test_postgres_session_store.py``).

Prior art: ``test_session.py`` for the in-memory gateway pattern.
"""

from __future__ import annotations

import time

from copilot.conversations import (
    ConversationRegistry,
    ConversationRow,
    InMemoryConversationStore,
    derive_title_from_message,
)


def _registry() -> ConversationRegistry:
    return ConversationRegistry(store=InMemoryConversationStore())


# ---------- derive_title_from_message ----------


def test_derive_title_truncates_to_60_chars() -> None:
    long_text = "A" * 200
    title = derive_title_from_message(long_text)
    assert len(title) <= 60


def test_derive_title_strips_whitespace() -> None:
    assert derive_title_from_message("   hello world   ") == "hello world"


def test_derive_title_collapses_internal_newlines() -> None:
    """Sidebar can't render multi-line titles — collapse to a single line."""
    title = derive_title_from_message("line one\nline two")
    assert "\n" not in title


def test_derive_title_falls_back_for_empty_input() -> None:
    """Empty / whitespace-only input still produces a non-empty title so the
    sidebar row isn't blank. The exact fallback string isn't part of the
    contract; we just require non-empty."""
    assert derive_title_from_message("") != ""
    assert derive_title_from_message("   \n  ") != ""


def test_derive_title_under_60_passes_through() -> None:
    short = "Give me a brief on Eduardo"
    assert derive_title_from_message(short) == short


# ---------- create ----------


async def test_create_returns_row_with_supplied_id() -> None:
    """The row id must equal the LangGraph thread_id passed in."""
    reg = _registry()
    row = await reg.create(
        conversation_id="thread-abc-123",
        user_id="practitioner-dr-smith",
    )
    assert row.id == "thread-abc-123"
    assert row.user_id == "practitioner-dr-smith"
    assert row.archived_at is None


async def test_create_initializes_timestamps() -> None:
    reg = _registry()
    before = time.time()
    row = await reg.create(
        conversation_id="thread-1",
        user_id="user-1",
    )
    after = time.time()
    assert before <= row.created_at <= after
    assert row.updated_at == row.created_at


async def test_create_initializes_with_empty_focus_pid() -> None:
    """A fresh conversation has no focus until the first resolve_patient."""
    reg = _registry()
    row = await reg.create(conversation_id="thread-fresh", user_id="user-1")
    assert row.last_focus_pid == ""


# ---------- list_for_user ----------


async def test_list_scopes_to_user() -> None:
    """Rows for other users must not leak into a user's sidebar."""
    reg = _registry()
    await reg.create(conversation_id="t-mine-1", user_id="user-a")
    await reg.create(conversation_id="t-mine-2", user_id="user-a")
    await reg.create(conversation_id="t-other", user_id="user-b")

    mine = await reg.list_for_user("user-a")
    ids = sorted(r.id for r in mine)
    assert ids == ["t-mine-1", "t-mine-2"]


async def test_list_ordered_by_updated_at_desc() -> None:
    """Most-recently-touched conversation appears first in the sidebar."""
    reg = _registry()
    await reg.create(conversation_id="t-old", user_id="u")
    # Force a measurable gap so the touch's monotonic guard advances even on
    # systems where time.time() doesn't tick between back-to-back calls.
    import asyncio

    await asyncio.sleep(0.01)
    await reg.create(conversation_id="t-mid", user_id="u")
    await asyncio.sleep(0.01)
    await reg.create(conversation_id="t-new", user_id="u")

    rows = await reg.list_for_user("u")
    assert [r.id for r in rows] == ["t-new", "t-mid", "t-old"]


async def test_list_excludes_archived_rows() -> None:
    reg = _registry()
    await reg.create(conversation_id="t-active", user_id="u")
    archived = await reg.create(conversation_id="t-archived", user_id="u")
    await reg.archive(archived.id)

    rows = await reg.list_for_user("u")
    assert [r.id for r in rows] == ["t-active"]


async def test_list_for_unknown_user_is_empty() -> None:
    reg = _registry()
    assert await reg.list_for_user("nobody") == []


# ---------- get ----------


async def test_get_returns_full_row() -> None:
    reg = _registry()
    created = await reg.create(conversation_id="t-1", user_id="u")
    fetched = await reg.get("t-1")
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.user_id == created.user_id


async def test_get_unknown_id_returns_none() -> None:
    reg = _registry()
    assert await reg.get("never-created") is None


# ---------- touch ----------


async def test_touch_advances_updated_at() -> None:
    """Every chat turn calls touch — sidebar ordering relies on updated_at."""
    import asyncio

    reg = _registry()
    row = await reg.create(conversation_id="t-touch", user_id="u")
    original_updated = row.updated_at
    await asyncio.sleep(0.01)

    await reg.touch("t-touch", focus_pid="fixture-1")
    fresh = await reg.get("t-touch")
    assert fresh is not None
    assert fresh.updated_at > original_updated
    assert fresh.last_focus_pid == "fixture-1"


async def test_touch_preserves_existing_focus_when_pid_empty() -> None:
    """A turn that doesn't change focus shouldn't blank the existing pid."""
    reg = _registry()
    await reg.create(conversation_id="t-keep", user_id="u")
    await reg.touch("t-keep", focus_pid="fixture-3")
    await reg.touch("t-keep", focus_pid="")  # turn with no resolution

    fresh = await reg.get("t-keep")
    assert fresh is not None
    assert fresh.last_focus_pid == "fixture-3"


async def test_touch_unknown_id_is_noop() -> None:
    """Touching a non-existent row must not raise — the chat path may
    legitimately be ahead of the registry create on the very first turn."""
    reg = _registry()
    await reg.touch("never-exists", focus_pid="fixture-1")  # should not raise


# ---------- set_title ----------


async def test_set_title_overwrites_existing_title() -> None:
    reg = _registry()
    await reg.create(conversation_id="t-title", user_id="u")
    await reg.set_title("t-title", "Brief on Eduardo Perez")
    fresh = await reg.get("t-title")
    assert fresh is not None
    assert fresh.title == "Brief on Eduardo Perez"


async def test_set_title_truncates_oversized_input() -> None:
    """Defense-in-depth — caller normally truncates first, but the registry
    should also enforce the 60-char ceiling so a misbehaving caller can't
    blow up the sidebar layout."""
    reg = _registry()
    await reg.create(conversation_id="t-bigtitle", user_id="u")
    await reg.set_title("t-bigtitle", "Z" * 500)
    fresh = await reg.get("t-bigtitle")
    assert fresh is not None
    assert len(fresh.title) <= 60


# ---------- ensure_first_turn_title ----------


async def test_ensure_first_turn_title_sets_when_blank() -> None:
    """The /chat endpoint calls this on every turn; only the first turn
    actually writes the title."""
    reg = _registry()
    await reg.create(conversation_id="t-first", user_id="u")
    written = await reg.ensure_first_turn_title(
        "t-first",
        "Tell me about Eduardo",
    )
    assert written is True
    fresh = await reg.get("t-first")
    assert fresh is not None
    assert fresh.title == "Tell me about Eduardo"


async def test_ensure_first_turn_title_skips_when_already_set() -> None:
    """Subsequent turns must not overwrite the first-turn-derived title."""
    reg = _registry()
    await reg.create(conversation_id="t-second", user_id="u")
    first = await reg.ensure_first_turn_title("t-second", "first turn message")
    assert first is True

    second = await reg.ensure_first_turn_title("t-second", "totally different second turn")
    assert second is False  # no write
    fresh = await reg.get("t-second")
    assert fresh is not None
    assert fresh.title == "first turn message"


async def test_ensure_first_turn_title_is_safe_on_unknown_id() -> None:
    """Mirrors touch's no-op-on-missing-row semantics — the chat path may
    fire title write before the registry create completes."""
    reg = _registry()
    written = await reg.ensure_first_turn_title("never", "msg")
    assert written is False


# ---------- ConversationRow construction ----------


def test_conversation_row_archived_default_is_none() -> None:
    """Frozen dataclass: callers shouldn't have to remember to pass None."""
    now = time.time()
    row = ConversationRow(
        id="t-1",
        user_id="u",
        title="hi",
        last_focus_pid="",
        created_at=now,
        updated_at=now,
    )
    assert row.archived_at is None
