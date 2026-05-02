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

import base64
import os
import time
import uuid

import pytest

from copilot.session import (
    LaunchStateRow,
    PostgresSessionStore,
    SessionRow,
    TokenBundleRow,
    open_session_store,
)
from copilot.token_crypto import TokenEncryptor, load_encryptor_from_env

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
    monkeypatch.setenv(
        "COPILOT_TOKEN_ENC_KEY",
        base64.b64encode(b"\x00" * 32).decode("ascii"),
    )

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


# ---------- Encryption-at-rest (issue 009) ----------


_TEST_KEY_B64 = base64.b64encode(b"\x00" * 32).decode("ascii")


def _encryptor() -> TokenEncryptor:
    return load_encryptor_from_env({"COPILOT_TOKEN_ENC_KEY": _TEST_KEY_B64})


async def test_token_columns_are_ciphertext_in_postgres() -> None:
    """The persisted column value must NOT match the plaintext token —
    a SELECT directly against the table sees ``enc1:<base64>`` blobs.
    """
    async with open_session_store(_DSN, encryptor=_encryptor()) as store:
        sid = f"sess-enc-{_row_id()}"
        now = time.time()
        await store.put_token_bundle(
            TokenBundleRow(
                session_id=sid,
                access_token="at-secret-XYZ",
                refresh_token="rt-secret-ABC",
                id_token="id.secret.JWT",
                scope="openid",
                issuer="https://openemr.example",
                expires_at=now + 3600,
            )
        )
        # Directly inspect the row — bypasses get_token_bundle's decrypt.
        async with store._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT access_token, refresh_token, id_token
                    FROM copilot_token_bundle WHERE session_id = %s
                    """,
                    (sid,),
                )
                stored_access, stored_refresh, stored_id = await cur.fetchone()
        for stored, plaintext in [
            (stored_access, "at-secret-XYZ"),
            (stored_refresh, "rt-secret-ABC"),
            (stored_id, "id.secret.JWT"),
        ]:
            assert plaintext not in stored
            assert stored.startswith("enc1:")

        # And get_token_bundle still returns the plaintext transparently.
        got = await store.get_token_bundle(sid)
        assert got is not None
        assert got.access_token == "at-secret-XYZ"
        assert got.refresh_token == "rt-secret-ABC"
        assert got.id_token == "id.secret.JWT"


async def test_migration_encrypts_pre_existing_plaintext_rows() -> None:
    """Insert a plaintext row (mimicking issue 001 leftovers), run the
    migration, verify the row is ciphertext in storage but still
    decrypts to the original plaintext on read."""
    sid = f"sess-mig-{_row_id()}"
    now = time.time()

    # 1. Write a plaintext bundle (encryptor=None — legacy behaviour).
    async with open_session_store(_DSN) as plain_store:
        await plain_store.put_token_bundle(
            TokenBundleRow(
                session_id=sid,
                access_token="plain-at",
                refresh_token="plain-rt",
                id_token="plain-id",
                scope="openid",
                issuer="https://openemr.example",
                expires_at=now + 3600,
            )
        )

    # 2. Reopen with an encryptor — open_session_store runs the migration
    #    automatically, so the row is encrypted in place.
    async with open_session_store(_DSN, encryptor=_encryptor()) as enc_store:
        async with enc_store._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT access_token, refresh_token, id_token
                    FROM copilot_token_bundle WHERE session_id = %s
                    """,
                    (sid,),
                )
                stored_access, stored_refresh, stored_id = await cur.fetchone()
        assert "plain-at" not in stored_access
        assert "plain-rt" not in stored_refresh
        assert "plain-id" not in stored_id
        assert stored_access.startswith("enc1:")
        assert stored_refresh.startswith("enc1:")
        assert stored_id.startswith("enc1:")

        # Round-trip read returns the original values.
        got = await enc_store.get_token_bundle(sid)
        assert got is not None
        assert got.access_token == "plain-at"
        assert got.refresh_token == "plain-rt"
        assert got.id_token == "plain-id"


async def test_migration_is_idempotent() -> None:
    """A second migration pass must not double-encrypt or rewrite rows
    that already carry the ``enc1:`` prefix."""
    sid = f"sess-mig-idem-{_row_id()}"
    now = time.time()

    async with open_session_store(_DSN, encryptor=_encryptor()) as store:
        await store.put_token_bundle(
            TokenBundleRow(
                session_id=sid,
                access_token="hello",
                refresh_token="world",
                id_token="id",
                scope="openid",
                issuer="https://openemr.example",
                expires_at=now + 3600,
            )
        )
        # Call migration directly — already-encrypted rows are skipped.
        rewritten = await store.encrypt_existing_token_bundles()
        assert rewritten == 0
        got = await store.get_token_bundle(sid)
        assert got is not None
        assert got.access_token == "hello"


async def test_tampered_ciphertext_drops_row_and_returns_none() -> None:
    """A row whose ciphertext was tampered with cannot be decrypted; the
    store deletes it and returns ``None`` so the next attempt is a clean
    miss → re-login. This is the headline at-rest property: a database
    rewrite cannot mint working tokens."""
    sid = f"sess-tamper-{_row_id()}"
    now = time.time()

    async with open_session_store(_DSN, encryptor=_encryptor()) as store:
        await store.put_token_bundle(
            TokenBundleRow(
                session_id=sid,
                access_token="real-at",
                refresh_token="real-rt",
                id_token="real-id",
                scope="openid",
                issuer="https://openemr.example",
                expires_at=now + 3600,
            )
        )

        # Tamper directly in the table — flip one byte of the access token
        # ciphertext after the prefix.
        async with store._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT access_token FROM copilot_token_bundle "
                    "WHERE session_id = %s",
                    (sid,),
                )
                (stored,) = await cur.fetchone()
                tampered = stored[:-2] + ("A" if stored[-1] != "A" else "B") + stored[-1]
                await cur.execute(
                    "UPDATE copilot_token_bundle SET access_token = %s "
                    "WHERE session_id = %s",
                    (tampered, sid),
                )

        got = await store.get_token_bundle(sid)
        assert got is None  # decryption failed → row treated as missing

        # Row was deleted by the get path, so the next read is also None.
        async with store._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM copilot_token_bundle WHERE session_id = %s",
                    (sid,),
                )
                assert await cur.fetchone() is None


async def test_migration_leaves_already_encrypted_columns_untouched() -> None:
    """Mixed-mode rows (one column encrypted, two plaintext) — only the
    plaintext columns are rewritten; the already-encrypted column keeps
    its original ciphertext bit-for-bit (no fresh nonce). Establishes
    that the migration only writes what it needs to."""
    sid = f"sess-mixed-{_row_id()}"
    now = time.time()
    enc = _encryptor()
    pre_encrypted = enc.encrypt("already-encrypted-id")

    # Manually craft a row: access plaintext, refresh plaintext, id encrypted.
    async with open_session_store(_DSN) as plain_store:
        async with plain_store._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO copilot_token_bundle
                        (session_id, access_token, refresh_token, id_token,
                         scope, issuer, expires_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        sid,
                        "plain-at",
                        "plain-rt",
                        pre_encrypted,
                        "openid",
                        "https://openemr.example",
                        now + 3600,
                    ),
                )

    # Now reopen with the same encryptor — migration encrypts the two
    # plaintext columns, leaves the third alone.
    async with open_session_store(_DSN, encryptor=enc) as enc_store:
        async with enc_store._pool.connection() as conn:  # type: ignore[attr-defined]
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT access_token, refresh_token, id_token
                    FROM copilot_token_bundle WHERE session_id = %s
                    """,
                    (sid,),
                )
                stored_access, stored_refresh, stored_id = await cur.fetchone()
        assert stored_access.startswith("enc1:")
        assert stored_refresh.startswith("enc1:")
        # The id column was already encrypted — its bytes should match
        # the original ciphertext (no re-encryption with a fresh nonce).
        assert stored_id == pre_encrypted

        got = await enc_store.get_token_bundle(sid)
        assert got is not None
        assert got.access_token == "plain-at"
        assert got.refresh_token == "plain-rt"
        assert got.id_token == "already-encrypted-id"


async def test_pool_construction_with_encryptor() -> None:
    """``PostgresSessionStore(pool, encryptor=...)`` accepts the encryptor
    via keyword arg and stores it for use by put / get."""
    async with open_session_store(_DSN) as bare_store:
        # Construct a sibling store directly against the same pool, with
        # an encryptor — exercises the kw-only ``encryptor`` parameter.
        encrypted_store = PostgresSessionStore(
            bare_store._pool, encryptor=_encryptor()
        )
        sid = f"sess-direct-{_row_id()}"
        now = time.time()
        await encrypted_store.put_token_bundle(
            TokenBundleRow(
                session_id=sid,
                access_token="direct-at",
                refresh_token="direct-rt",
                id_token="direct-id",
                scope="openid",
                issuer="https://openemr.example",
                expires_at=now + 3600,
            )
        )
        got = await encrypted_store.get_token_bundle(sid)
        assert got is not None
        assert got.access_token == "direct-at"


