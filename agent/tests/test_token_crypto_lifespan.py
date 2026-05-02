"""Token-encryption startup-hook tests.

The agent backend must refuse to start when ``CHECKPOINTER_DSN`` is set
but ``COPILOT_TOKEN_ENC_KEY`` is missing or mis-shaped — there is no
implicit fallback to plaintext storage. These tests stub out the
checkpointer and session-store opens so the failure surfaces before any
real Postgres connection is attempted.

In-memory mode (no DSN) does not require the key — we don't persist
tokens in that path, so insisting on a key would block dev / fixture
flows. That non-requirement is also asserted here so a future change
that broadens the requirement doesn't slip in silently.
"""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from copilot import server as server_mod
from copilot.token_crypto import TokenEncryptionKeyInvalidError


def _stub_lifespan_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace heavy lifespan deps (checkpointer, session-store opens,
    graph) with no-ops so the test exercises only the key-loading path.
    """
    @asynccontextmanager
    async def _no_checkpointer(_settings):
        yield None

    @asynccontextmanager
    async def _no_session_store(_dsn, *, encryptor=None):
        # If we get here, the encryptor was successfully built.
        class _StubStore:
            pass

        yield _StubStore()

    @asynccontextmanager
    async def _no_conv_store(_dsn):
        class _StubStore:
            async def list_for_user(self, *_a, **_kw):
                return []

        yield _StubStore()

    monkeypatch.setattr(server_mod, "open_checkpointer", _no_checkpointer)
    monkeypatch.setattr(server_mod, "open_session_store", _no_session_store)
    monkeypatch.setattr(server_mod, "open_conversation_store", _no_conv_store)
    monkeypatch.setattr(server_mod, "build_graph", lambda *_a, **_kw: None)
    # Strip any pre-existing app state from a previous test.
    for attr in (
        "session_gateway",
        "conversation_registry",
        "title_summarizer",
    ):
        if hasattr(server_mod.app.state, attr):
            delattr(server_mod.app.state, attr)


def test_lifespan_fails_when_dsn_set_but_encryption_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``CHECKPOINTER_DSN`` set + ``COPILOT_TOKEN_ENC_KEY`` unset → loud
    failure naming the missing variable, no silent plaintext fallback."""
    monkeypatch.setenv("CHECKPOINTER_DSN", "postgresql://stub/stub")
    monkeypatch.setenv("USE_FIXTURE_FHIR", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("COPILOT_TOKEN_ENC_KEY", raising=False)

    _stub_lifespan_deps(monkeypatch)

    with pytest.raises(TokenEncryptionKeyInvalidError) as exc_info:
        with TestClient(server_mod.app):
            pass
    assert "COPILOT_TOKEN_ENC_KEY" in str(exc_info.value)


def test_lifespan_fails_when_encryption_key_is_wrong_length(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mis-shaped key (16 bytes instead of 32) fails on startup with the
    env var named — operator gets actionable diagnostics."""
    monkeypatch.setenv("CHECKPOINTER_DSN", "postgresql://stub/stub")
    monkeypatch.setenv("USE_FIXTURE_FHIR", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv(
        "COPILOT_TOKEN_ENC_KEY",
        base64.b64encode(b"\x00" * 16).decode("ascii"),  # 128-bit, not 256
    )

    _stub_lifespan_deps(monkeypatch)

    with pytest.raises(TokenEncryptionKeyInvalidError) as exc_info:
        with TestClient(server_mod.app):
            pass
    assert "COPILOT_TOKEN_ENC_KEY" in str(exc_info.value)


def test_lifespan_fails_when_encryption_key_is_not_base64(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CHECKPOINTER_DSN", "postgresql://stub/stub")
    monkeypatch.setenv("USE_FIXTURE_FHIR", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("COPILOT_TOKEN_ENC_KEY", "not!base64@@@")

    _stub_lifespan_deps(monkeypatch)

    with pytest.raises(TokenEncryptionKeyInvalidError):
        with TestClient(server_mod.app):
            pass


def test_lifespan_succeeds_when_dsn_set_and_key_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path: with both DSN and a valid 32-byte key, lifespan
    completes and the app starts."""
    monkeypatch.setenv("CHECKPOINTER_DSN", "postgresql://stub/stub")
    monkeypatch.setenv("USE_FIXTURE_FHIR", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv(
        "COPILOT_TOKEN_ENC_KEY",
        base64.b64encode(b"\x00" * 32).decode("ascii"),
    )

    _stub_lifespan_deps(monkeypatch)

    # No exception — TestClient enters lifespan cleanly.
    with TestClient(server_mod.app):
        pass


def test_lifespan_does_not_require_key_in_memory_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-memory mode (no DSN) does not persist tokens to disk — the
    encryption key is intentionally not required, so dev and fixture
    flows keep working without operator setup."""
    monkeypatch.delenv("CHECKPOINTER_DSN", raising=False)
    monkeypatch.delenv("COPILOT_TOKEN_ENC_KEY", raising=False)
    monkeypatch.setenv("USE_FIXTURE_FHIR", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    _stub_lifespan_deps(monkeypatch)

    with TestClient(server_mod.app):
        pass
