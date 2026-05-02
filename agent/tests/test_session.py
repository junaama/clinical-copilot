"""SessionGateway — standalone login session management tests.

Exercises the SessionGateway through its public interface using the in-memory
backend so tests run without Postgres. Covers:
- Launch state round-trip (create → pop → replayed rejection)
- Expired launch state rejection
- Session creation, retrieval, and deletion (logout)
- Token bundle storage and retrieval via session
- Expired session returns None
- fhirUser parsing
- Token refresh (mocked)

Prior art: test_smart.py for the EHR-launch PKCE/store pattern.
"""

from __future__ import annotations

import time

from copilot.session import (
    InMemorySessionStore,
    LaunchStateRow,
    SessionGateway,
    SessionRow,
    TokenBundleRow,
    parse_fhir_user,
)


def _gateway() -> SessionGateway:
    return SessionGateway(store=InMemorySessionStore())


# ---------- fhirUser parsing ----------

def test_parse_fhir_user_practitioner() -> None:
    assert parse_fhir_user("Practitioner/abc-123-uuid") == (
        "Practitioner",
        "abc-123-uuid",
    )


def test_parse_fhir_user_with_base_url() -> None:
    resource_type, uuid = parse_fhir_user(
        "https://openemr.example/fhir/Practitioner/abc-123"
    )
    assert resource_type == "Practitioner"
    assert uuid == "abc-123"


def test_parse_fhir_user_empty() -> None:
    assert parse_fhir_user("") == ("", "")


def test_parse_fhir_user_invalid() -> None:
    assert parse_fhir_user("garbage") == ("", "")


# ---------- Launch state ----------

async def test_launch_state_round_trip() -> None:
    gw = _gateway()
    now = time.time()
    await gw.create_launch_state(
        LaunchStateRow(
            state="state-1",
            code_verifier="verifier-abc",
            redirect_uri="http://localhost:5173/",
            expires_at=now + 600,
        )
    )
    row = await gw.pop_launch_state("state-1")
    assert row is not None
    assert row.code_verifier == "verifier-abc"
    assert row.redirect_uri == "http://localhost:5173/"


async def test_launch_state_replayed_rejected() -> None:
    """Pop is one-shot — a second pop for the same state returns None."""
    gw = _gateway()
    await gw.create_launch_state(
        LaunchStateRow(
            state="state-2",
            code_verifier="v",
            redirect_uri="",
            expires_at=time.time() + 600,
        )
    )
    assert await gw.pop_launch_state("state-2") is not None
    assert await gw.pop_launch_state("state-2") is None


async def test_launch_state_unknown_rejected() -> None:
    gw = _gateway()
    assert await gw.pop_launch_state("never-issued") is None


async def test_launch_state_expired_rejected() -> None:
    gw = _gateway()
    await gw.create_launch_state(
        LaunchStateRow(
            state="state-exp",
            code_verifier="v",
            redirect_uri="",
            expires_at=time.time() - 1,  # already expired
        )
    )
    assert await gw.pop_launch_state("state-exp") is None


# ---------- Session CRUD ----------

async def test_session_create_and_get() -> None:
    gw = _gateway()
    now = time.time()
    session = SessionRow(
        session_id="sess-1",
        oe_user_id=42,
        display_name="Dr. Smith",
        fhir_user="Practitioner/abc-123",
        created_at=now,
        expires_at=now + 3600,
    )
    await gw.create_session(session)
    got = await gw.get_session("sess-1")
    assert got is not None
    assert got.oe_user_id == 42
    assert got.display_name == "Dr. Smith"
    assert got.fhir_user == "Practitioner/abc-123"


async def test_session_expired_returns_none() -> None:
    gw = _gateway()
    now = time.time()
    session = SessionRow(
        session_id="sess-exp",
        oe_user_id=1,
        display_name="Expired Dr.",
        fhir_user="Practitioner/xyz",
        created_at=now - 7200,
        expires_at=now - 1,  # already expired
    )
    await gw.create_session(session)
    assert await gw.get_session("sess-exp") is None


async def test_session_unknown_returns_none() -> None:
    gw = _gateway()
    assert await gw.get_session("no-such-session") is None


async def test_session_delete_logout() -> None:
    gw = _gateway()
    now = time.time()
    session = SessionRow(
        session_id="sess-del",
        oe_user_id=1,
        display_name="Dr. Logout",
        fhir_user="Practitioner/xyz",
        created_at=now,
        expires_at=now + 3600,
    )
    await gw.create_session(session)
    await gw.delete_session("sess-del")
    assert await gw.get_session("sess-del") is None


async def test_delete_nonexistent_session_is_noop() -> None:
    gw = _gateway()
    # Should not raise
    await gw.delete_session("no-such-session")


# ---------- Token bundle ----------

async def test_token_bundle_round_trip() -> None:
    gw = _gateway()
    now = time.time()
    bundle = TokenBundleRow(
        session_id="sess-tok",
        access_token="at-xyz",
        refresh_token="rt-abc",
        id_token="id.jwt.here",
        scope="openid fhirUser user/*.rs",
        issuer="https://openemr.example",
        expires_at=now + 3600,
    )
    await gw.upsert_token_bundle(bundle)
    got = await gw.get_token_bundle("sess-tok")
    assert got is not None
    assert got.access_token == "at-xyz"
    assert got.refresh_token == "rt-abc"


async def test_token_bundle_overwrite() -> None:
    gw = _gateway()
    now = time.time()
    b1 = TokenBundleRow(
        session_id="sess-ow",
        access_token="at-1",
        refresh_token="rt-1",
        id_token="id-1",
        scope="scope-1",
        issuer="iss",
        expires_at=now + 3600,
    )
    await gw.upsert_token_bundle(b1)
    b2 = TokenBundleRow(
        session_id="sess-ow",
        access_token="at-2",
        refresh_token="rt-2",
        id_token="id-2",
        scope="scope-2",
        issuer="iss",
        expires_at=now + 7200,
    )
    await gw.upsert_token_bundle(b2)
    got = await gw.get_token_bundle("sess-ow")
    assert got is not None
    assert got.access_token == "at-2"


async def test_token_bundle_unknown_session() -> None:
    gw = _gateway()
    assert await gw.get_token_bundle("no-such") is None


# ---------- Full login round-trip ----------

# ---------- Token refresh ----------


async def test_get_fresh_token_bundle_returns_existing_when_unexpired() -> None:
    """The hot path: token has plenty of life left, so the gateway returns
    the stored bundle without invoking the refresh callable."""
    gw = _gateway()
    now = time.time()
    await gw.upsert_token_bundle(
        TokenBundleRow(
            session_id="sess-fresh",
            access_token="at-current",
            refresh_token="rt-current",
            id_token="id.current",
            scope="openid fhirUser user/*.rs",
            issuer="https://openemr.example",
            expires_at=now + 3600,  # comfortably alive
        )
    )

    refresh_calls: list[str] = []

    async def _should_not_refresh(_rt: str) -> dict[str, object]:
        refresh_calls.append("called")
        return {}

    fresh = await gw.get_fresh_token_bundle(
        "sess-fresh", refresh_fn=_should_not_refresh
    )
    assert fresh is not None
    assert fresh.access_token == "at-current"
    assert refresh_calls == []  # no refresh round-trip


async def test_get_fresh_token_bundle_refreshes_within_skew() -> None:
    """When ``expires_at`` is within the skew window, the gateway calls
    ``refresh_fn`` with the stored refresh token and persists the new bundle.
    """
    gw = _gateway()
    now = time.time()
    await gw.upsert_token_bundle(
        TokenBundleRow(
            session_id="sess-skew",
            access_token="at-stale",
            refresh_token="rt-old",
            id_token="id.stale",
            scope="openid fhirUser user/*.rs",
            issuer="https://openemr.example",
            expires_at=now + 10,  # inside the default 30s skew
        )
    )

    refresh_calls: list[str] = []

    async def _refresh(rt: str) -> dict[str, object]:
        refresh_calls.append(rt)
        return {
            "access_token": "at-new",
            "refresh_token": "rt-new",
            "id_token": "id.new",
            "scope": "openid fhirUser user/*.rs",
            "expires_in": 3600,
        }

    fresh = await gw.get_fresh_token_bundle("sess-skew", refresh_fn=_refresh)
    assert fresh is not None
    assert fresh.access_token == "at-new"
    assert fresh.refresh_token == "rt-new"
    assert refresh_calls == ["rt-old"]  # called exactly once with old rt

    # Persisted: a subsequent unexpired-path read returns the rotated bundle.
    persisted = await gw.get_token_bundle("sess-skew")
    assert persisted is not None
    assert persisted.access_token == "at-new"
    assert persisted.refresh_token == "rt-new"
    assert persisted.expires_at > now + 3000


async def test_get_fresh_token_bundle_refreshes_when_already_expired() -> None:
    """A frankly-expired bundle (clock past ``expires_at``) still refreshes —
    the skew logic is upper-bound, not lower-bound."""
    gw = _gateway()
    now = time.time()
    await gw.upsert_token_bundle(
        TokenBundleRow(
            session_id="sess-expired",
            access_token="at-dead",
            refresh_token="rt-still-good",
            id_token="id.dead",
            scope="openid fhirUser",
            issuer="https://openemr.example",
            expires_at=now - 600,  # 10 min past expiry
        )
    )

    async def _refresh(_rt: str) -> dict[str, object]:
        return {
            "access_token": "at-new",
            "refresh_token": "rt-new",
            "expires_in": 3600,
        }

    fresh = await gw.get_fresh_token_bundle(
        "sess-expired", refresh_fn=_refresh
    )
    assert fresh is not None
    assert fresh.access_token == "at-new"


async def test_get_fresh_token_bundle_preserves_old_refresh_when_not_rotated() -> None:
    """Some token endpoints don't rotate the refresh token on a refresh call.
    The gateway must keep the old refresh token rather than overwriting it
    with an empty string — otherwise the next refresh fails with
    ``invalid_grant``."""
    gw = _gateway()
    now = time.time()
    await gw.upsert_token_bundle(
        TokenBundleRow(
            session_id="sess-no-rotate",
            access_token="at-stale",
            refresh_token="rt-keep-me",
            id_token="id.kept",
            scope="openid fhirUser",
            issuer="https://openemr.example",
            expires_at=now + 5,
        )
    )

    async def _refresh(_rt: str) -> dict[str, object]:
        # Note: no ``refresh_token`` key in the response.
        return {
            "access_token": "at-new",
            "id_token": "id.kept",  # also unchanged
            "expires_in": 3600,
        }

    fresh = await gw.get_fresh_token_bundle(
        "sess-no-rotate", refresh_fn=_refresh
    )
    assert fresh is not None
    assert fresh.access_token == "at-new"
    assert fresh.refresh_token == "rt-keep-me"  # carried over


async def test_get_fresh_token_bundle_returns_none_when_session_has_no_bundle() -> None:
    """No bundle = no refresh attempted; caller treats this as "needs login"
    rather than as a refresh failure."""
    gw = _gateway()

    async def _refresh(_rt: str) -> dict[str, object]:
        raise AssertionError("refresh_fn called for missing bundle")

    fresh = await gw.get_fresh_token_bundle("sess-missing", refresh_fn=_refresh)
    assert fresh is None


async def test_get_fresh_token_bundle_propagates_refresh_failure() -> None:
    """Token endpoint rejection (e.g. ``invalid_grant`` because the refresh
    token was revoked) propagates; the caller forces the user back through
    /auth/login.  The stale bundle is left in place — clearing it is the
    caller's job after they decide what to do."""
    gw = _gateway()
    now = time.time()
    await gw.upsert_token_bundle(
        TokenBundleRow(
            session_id="sess-fail",
            access_token="at-stale",
            refresh_token="rt-revoked",
            id_token="id",
            scope="openid",
            issuer="https://openemr.example",
            expires_at=now - 1,
        )
    )

    async def _refresh(_rt: str) -> dict[str, object]:
        raise RuntimeError("token endpoint refused refresh (400): invalid_grant")

    import pytest

    with pytest.raises(RuntimeError):
        await gw.get_fresh_token_bundle("sess-fail", refresh_fn=_refresh)

    # Stale bundle survives so a debug operator can inspect it.
    stale = await gw.get_token_bundle("sess-fail")
    assert stale is not None
    assert stale.access_token == "at-stale"


async def test_full_login_round_trip() -> None:
    """Simulate the happy path: create launch state → pop it → create session
    + token bundle → /me reads session → logout deletes it."""
    gw = _gateway()
    now = time.time()

    # 1. Create launch state (what /auth/login does)
    await gw.create_launch_state(
        LaunchStateRow(
            state="state-full",
            code_verifier="pkce-verifier",
            redirect_uri="http://localhost:5173/",
            expires_at=now + 600,
        )
    )

    # 2. Pop launch state (what /auth/smart/callback does)
    ls = await gw.pop_launch_state("state-full")
    assert ls is not None

    # 3. Create session (after code exchange)
    session = SessionRow(
        session_id="sess-full",
        oe_user_id=42,
        display_name="Dr. Smith",
        fhir_user="Practitioner/abc-123",
        created_at=now,
        expires_at=now + 28800,
    )
    await gw.create_session(session)

    # 4. Store tokens
    await gw.upsert_token_bundle(
        TokenBundleRow(
            session_id="sess-full",
            access_token="at-real",
            refresh_token="rt-real",
            id_token="id.real.jwt",
            scope="openid fhirUser user/*.rs",
            issuer="https://openemr.example",
            expires_at=now + 3600,
        )
    )

    # 5. Verify /me path works
    got_session = await gw.get_session("sess-full")
    assert got_session is not None
    assert got_session.display_name == "Dr. Smith"

    got_tokens = await gw.get_token_bundle("sess-full")
    assert got_tokens is not None
    assert got_tokens.access_token == "at-real"

    # 6. Logout
    await gw.delete_session("sess-full")
    assert await gw.get_session("sess-full") is None
