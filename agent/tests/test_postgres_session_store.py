"""Postgres-backed SessionStore integration test.

Skipped when ``COPILOT_TEST_PG_DSN`` isn't set. Run manually after starting
a disposable Postgres:

    docker run --rm -d --name copilot-pg -p 5442:5432 \\
      -e POSTGRES_PASSWORD=postgres -e POSTGRES_USER=postgres \\
      -e POSTGRES_DB=postgres postgres:16-alpine

    COPILOT_TEST_PG_DSN='postgresql://postgres:postgres@localhost:5442/postgres?sslmode=disable' \\
      uv run pytest tests/test_postgres_session_store.py -v

Mirrors ``test_postgres_checkpointer.py`` — gated by the same env var so a
single Postgres instance covers both.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from copilot.session import (
    LaunchStateRow,
    SessionRow,
    TokenBundleRow,
    open_session_store,
)

_DSN = os.environ.get("COPILOT_TEST_PG_DSN", "")

pytestmark = pytest.mark.skipif(
    not _DSN, reason="set COPILOT_TEST_PG_DSN to run this integration test"
)


def _row_id() -> str:
    """Per-test unique id so tests don't collide on shared rows."""
    return uuid.uuid4().hex[:12]


async def test_ensure_schema_is_idempotent() -> None:
    """Running ensure_schema twice on the same DSN must not error."""
    async with open_session_store(_DSN) as store:
        await store.ensure_schema()
        await store.ensure_schema()


async def test_launch_state_round_trip() -> None:
    async with open_session_store(_DSN) as store:
        state = f"state-{_row_id()}"
        now = time.time()
        await store.put_launch_state(
            LaunchStateRow(
                state=state,
                code_verifier="verifier-abc",
                redirect_uri="http://localhost:5173/",
                expires_at=now + 600,
            )
        )
        row = await store.pop_launch_state(state)
        assert row is not None
        assert row.code_verifier == "verifier-abc"
        assert row.redirect_uri == "http://localhost:5173/"
        # Pop is one-shot — second pop returns None.
        assert await store.pop_launch_state(state) is None


async def test_launch_state_expired_rejected() -> None:
    async with open_session_store(_DSN) as store:
        state = f"state-exp-{_row_id()}"
        await store.put_launch_state(
            LaunchStateRow(
                state=state,
                code_verifier="v",
                redirect_uri="",
                expires_at=time.time() - 1,
            )
        )
        assert await store.pop_launch_state(state) is None


async def test_launch_state_unknown_rejected() -> None:
    async with open_session_store(_DSN) as store:
        assert await store.pop_launch_state(f"never-issued-{_row_id()}") is None


async def test_session_round_trip() -> None:
    async with open_session_store(_DSN) as store:
        sid = f"sess-{_row_id()}"
        now = time.time()
        await store.put_session(
            SessionRow(
                session_id=sid,
                oe_user_id=42,
                display_name="Dr. Smith",
                fhir_user="Practitioner/abc-123",
                created_at=now,
                expires_at=now + 3600,
            )
        )
        got = await store.get_session(sid)
        assert got is not None
        assert got.oe_user_id == 42
        assert got.display_name == "Dr. Smith"
        assert got.fhir_user == "Practitioner/abc-123"


async def test_session_expired_lazy_evicted() -> None:
    async with open_session_store(_DSN) as store:
        sid = f"sess-exp-{_row_id()}"
        now = time.time()
        await store.put_session(
            SessionRow(
                session_id=sid,
                oe_user_id=1,
                display_name="Expired",
                fhir_user="Practitioner/x",
                created_at=now - 7200,
                expires_at=now - 1,
            )
        )
        assert await store.get_session(sid) is None


async def test_session_delete_logout() -> None:
    async with open_session_store(_DSN) as store:
        sid = f"sess-del-{_row_id()}"
        now = time.time()
        await store.put_session(
            SessionRow(
                session_id=sid,
                oe_user_id=1,
                display_name="Logout",
                fhir_user="Practitioner/x",
                created_at=now,
                expires_at=now + 3600,
            )
        )
        await store.delete_session(sid)
        assert await store.get_session(sid) is None


async def test_token_bundle_round_trip_and_upsert() -> None:
    async with open_session_store(_DSN) as store:
        sid = f"sess-tok-{_row_id()}"
        now = time.time()
        await store.put_token_bundle(
            TokenBundleRow(
                session_id=sid,
                access_token="at-1",
                refresh_token="rt-1",
                id_token="id-1",
                scope="openid",
                issuer="https://openemr.example",
                expires_at=now + 3600,
            )
        )
        got = await store.get_token_bundle(sid)
        assert got is not None
        assert got.access_token == "at-1"

        # Upsert path: same session_id with new tokens overwrites.
        await store.put_token_bundle(
            TokenBundleRow(
                session_id=sid,
                access_token="at-2",
                refresh_token="rt-2",
                id_token="id-2",
                scope="openid",
                issuer="https://openemr.example",
                expires_at=now + 7200,
            )
        )
        got = await store.get_token_bundle(sid)
        assert got is not None
        assert got.access_token == "at-2"
        assert got.refresh_token == "rt-2"


async def test_state_survives_across_store_instances() -> None:
    """Open store, write session, close pool, reopen, read it back.

    This is the headline durability property — a process restart must not
    log out every active user.
    """
    sid = f"sess-durable-{_row_id()}"
    now = time.time()
    session = SessionRow(
        session_id=sid,
        oe_user_id=99,
        display_name="Durable",
        fhir_user="Practitioner/persistent",
        created_at=now,
        expires_at=now + 3600,
    )

    async with open_session_store(_DSN) as store:
        await store.put_session(session)

    async with open_session_store(_DSN) as store2:
        got = await store2.get_session(sid)
        assert got is not None
        assert got.oe_user_id == 99
        assert got.display_name == "Durable"


async def test_delete_nonexistent_session_is_noop() -> None:
    async with open_session_store(_DSN) as store:
        await store.delete_session(f"no-such-{_row_id()}")


def test_lifespan_uses_postgres_store_when_dsn_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wiring check: with CHECKPOINTER_DSN set, lifespan picks the Postgres store.

    Boots the FastAPI app with the test DSN and verifies that
    ``app.state.session_gateway`` is backed by a ``PostgresSessionStore``,
    not the in-memory fallback.
    """
    from contextlib import asynccontextmanager

    from fastapi.testclient import TestClient

    from copilot import server as server_mod
    from copilot.session import PostgresSessionStore

    monkeypatch.setenv("CHECKPOINTER_DSN", _DSN)
    monkeypatch.setenv("USE_FIXTURE_FHIR", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    @asynccontextmanager
    async def _stub_checkpointer(_settings):
        yield None

    monkeypatch.setattr(server_mod, "build_graph", lambda *_a, **_kw: None)
    monkeypatch.setattr(server_mod, "open_checkpointer", _stub_checkpointer)
    # Strip any pre-existing gateway from another test's state.
    if hasattr(server_mod.app.state, "session_gateway"):
        delattr(server_mod.app.state, "session_gateway")

    with TestClient(server_mod.app):
        gateway = server_mod.app.state.session_gateway
        assert isinstance(gateway._store, PostgresSessionStore)
