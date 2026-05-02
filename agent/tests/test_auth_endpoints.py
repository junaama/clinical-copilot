"""Tests for the standalone auth endpoints.

Exercises GET /auth/login, GET /auth/smart/callback, GET /me, POST /auth/logout
using the FastAPI TestClient with in-memory session store and mocked SMART
discovery/exchange.  No real OpenEMR needed.

Prior art: test_chat_contract.py for the TestClient fixture pattern.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from copilot.session import InMemorySessionStore, SessionGateway


@pytest.fixture
def auth_client(monkeypatch: pytest.MonkeyPatch):
    """TestClient wired with standalone auth config and in-memory session store."""

    monkeypatch.setenv("USE_FIXTURE_FHIR", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SMART_STANDALONE_CLIENT_ID", "copilot-standalone")
    monkeypatch.setenv("SMART_STANDALONE_CLIENT_SECRET", "standalone-secret")
    monkeypatch.setenv(
        "SMART_STANDALONE_REDIRECT_URI",
        "http://localhost:8000/auth/smart/callback",
    )
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-32-bytes!!")
    monkeypatch.setenv("COPILOT_UI_URL", "http://localhost:5173")
    monkeypatch.setenv(
        "OPENEMR_FHIR_BASE",
        "https://openemr.example/apis/default/fhir",
    )

    from copilot import server as server_mod

    # Stub out graph/checkpointer
    @asynccontextmanager
    async def _stub_checkpointer(_settings):
        yield None

    monkeypatch.setattr(server_mod, "build_graph", lambda *_a, **_kw: None)
    monkeypatch.setattr(server_mod, "open_checkpointer", _stub_checkpointer)

    # Inject in-memory session store
    store = InMemorySessionStore()
    gateway = SessionGateway(store=store)

    with TestClient(server_mod.app) as client:
        server_mod.app.state.session_gateway = gateway
        yield client


# ---------- GET /auth/login ----------


def test_auth_login_redirects_to_authorize(auth_client: TestClient) -> None:
    """GET /auth/login returns a 302 to the OpenEMR authorize endpoint."""
    with patch(
        "copilot.server.discover_smart_endpoints",
        new_callable=AsyncMock,
        return_value={
            "authorization_endpoint": "https://openemr.example/oauth2/default/authorize",
            "token_endpoint": "https://openemr.example/oauth2/default/token",
        },
    ):
        resp = auth_client.get("/auth/login", follow_redirects=False)

    assert resp.status_code == 302
    location = resp.headers["location"]
    assert "openemr.example/oauth2/default/authorize" in location
    assert "response_type=code" in location
    assert "client_id=copilot-standalone" in location
    assert "code_challenge=" in location
    assert "code_challenge_method=S256" in location
    # No launch param (standalone, not EHR launch)
    assert "launch=" not in location


def test_auth_login_returns_503_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without SMART_STANDALONE_CLIENT_ID the endpoint is unavailable."""
    monkeypatch.setenv("USE_FIXTURE_FHIR", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SMART_STANDALONE_CLIENT_ID", "")

    from contextlib import asynccontextmanager

    from copilot import server as server_mod

    @asynccontextmanager
    async def _stub_checkpointer(_settings):
        yield None

    monkeypatch.setattr(server_mod, "build_graph", lambda *_a, **_kw: None)
    monkeypatch.setattr(server_mod, "open_checkpointer", _stub_checkpointer)

    store = InMemorySessionStore()
    gateway = SessionGateway(store=store)

    with TestClient(server_mod.app) as client:
        server_mod.app.state.session_gateway = gateway
        resp = client.get("/auth/login", follow_redirects=False)

    assert resp.status_code == 503


# ---------- GET /auth/smart/callback ----------


_MOCK_ID_TOKEN = (
    "header"
    ".eyJmaGlyVXNlciI6ICJQcmFjdGl0aW9uZXIvYWJjLTEyMy11dWlkIiw"
    "gInN1YiI6ICJkcl9zbWl0aCIsICJuYW1lIjogIkRyLiBTbWl0aCJ9"
    ".sig"
)


def _mock_token_response() -> dict[str, Any]:
    """Simulated token endpoint response with fhirUser in id_token."""
    return {
        "access_token": "at-standalone-xyz",
        "refresh_token": "rt-standalone-abc",
        "id_token": _MOCK_ID_TOKEN,
        "scope": "openid fhirUser launch/user user/Patient.rs",
        "expires_in": 3600,
        "user": "dr_smith",
    }


def test_auth_callback_sets_cookie_and_redirects(auth_client: TestClient) -> None:
    """Successful callback creates session, sets cookie, redirects to UI."""
    # First, hit /auth/login to create launch state
    with patch(
        "copilot.server.discover_smart_endpoints",
        new_callable=AsyncMock,
        return_value={
            "authorization_endpoint": "https://openemr.example/oauth2/default/authorize",
            "token_endpoint": "https://openemr.example/oauth2/default/token",
        },
    ):
        login_resp = auth_client.get("/auth/login", follow_redirects=False)
    assert login_resp.status_code == 302

    # Extract the state from the redirect URL
    from urllib.parse import parse_qs, urlparse

    location = login_resp.headers["location"]
    query = parse_qs(urlparse(location).query)
    state = query["state"][0]

    # Now simulate the callback with the same state
    with patch(
        "copilot.server.discover_smart_endpoints",
        new_callable=AsyncMock,
        return_value={
            "authorization_endpoint": "https://openemr.example/oauth2/default/authorize",
            "token_endpoint": "https://openemr.example/oauth2/default/token",
        },
    ), patch(
        "copilot.server.exchange_code_for_token",
        new_callable=AsyncMock,
        return_value=_mock_token_response(),
    ):
        callback_resp = auth_client.get(
            f"/auth/smart/callback?code=auth-code-1&state={state}",
            follow_redirects=False,
        )

    assert callback_resp.status_code == 302
    assert callback_resp.headers["location"].startswith("http://localhost:5173")

    # Cookie should be set
    cookies = callback_resp.cookies
    assert "copilot_session" in cookies


def test_auth_callback_rejects_unknown_state(auth_client: TestClient) -> None:
    """Callback with an unrecognized state param is rejected."""
    resp = auth_client.get(
        "/auth/smart/callback?code=fake&state=never-issued",
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "unknown or expired" in resp.json()["detail"].lower()


def test_auth_callback_rejects_replayed_state(auth_client: TestClient) -> None:
    """Second use of the same state is rejected (replay protection)."""
    with patch(
        "copilot.server.discover_smart_endpoints",
        new_callable=AsyncMock,
        return_value={
            "authorization_endpoint": "https://openemr.example/oauth2/default/authorize",
            "token_endpoint": "https://openemr.example/oauth2/default/token",
        },
    ):
        login_resp = auth_client.get("/auth/login", follow_redirects=False)

    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(login_resp.headers["location"]).query)["state"][0]

    with patch(
        "copilot.server.discover_smart_endpoints",
        new_callable=AsyncMock,
        return_value={
            "authorization_endpoint": "https://openemr.example/oauth2/default/authorize",
            "token_endpoint": "https://openemr.example/oauth2/default/token",
        },
    ), patch(
        "copilot.server.exchange_code_for_token",
        new_callable=AsyncMock,
        return_value=_mock_token_response(),
    ):
        # First use succeeds
        first = auth_client.get(
            f"/auth/smart/callback?code=c1&state={state}",
            follow_redirects=False,
        )
        assert first.status_code == 302

        # Second use fails
        second = auth_client.get(
            f"/auth/smart/callback?code=c2&state={state}",
            follow_redirects=False,
        )
        assert second.status_code == 400


# ---------- GET /me ----------


def test_me_returns_401_without_cookie(auth_client: TestClient) -> None:
    resp = auth_client.get("/me")
    assert resp.status_code == 401


def test_me_returns_user_info_with_valid_session(auth_client: TestClient) -> None:
    """After a successful login, GET /me returns user info."""
    # Do the full login flow
    with patch(
        "copilot.server.discover_smart_endpoints",
        new_callable=AsyncMock,
        return_value={
            "authorization_endpoint": "https://openemr.example/oauth2/default/authorize",
            "token_endpoint": "https://openemr.example/oauth2/default/token",
        },
    ):
        login_resp = auth_client.get("/auth/login", follow_redirects=False)

    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(login_resp.headers["location"]).query)["state"][0]

    with patch(
        "copilot.server.discover_smart_endpoints",
        new_callable=AsyncMock,
        return_value={
            "authorization_endpoint": "https://openemr.example/oauth2/default/authorize",
            "token_endpoint": "https://openemr.example/oauth2/default/token",
        },
    ), patch(
        "copilot.server.exchange_code_for_token",
        new_callable=AsyncMock,
        return_value=_mock_token_response(),
    ):
        callback_resp = auth_client.get(
            f"/auth/smart/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )

    # Extract the session cookie
    session_cookie = callback_resp.cookies.get("copilot_session")
    assert session_cookie

    # Use the cookie to call /me
    me_resp = auth_client.get("/me", cookies={"copilot_session": session_cookie})
    assert me_resp.status_code == 200
    body = me_resp.json()
    assert body["display_name"] == "Dr. Smith"
    assert "user_id" in body


# ---------- POST /auth/logout ----------


def test_logout_clears_session(auth_client: TestClient) -> None:
    """POST /auth/logout revokes session and clears the cookie."""
    # Do the full login flow
    with patch(
        "copilot.server.discover_smart_endpoints",
        new_callable=AsyncMock,
        return_value={
            "authorization_endpoint": "https://openemr.example/oauth2/default/authorize",
            "token_endpoint": "https://openemr.example/oauth2/default/token",
        },
    ):
        login_resp = auth_client.get("/auth/login", follow_redirects=False)

    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(login_resp.headers["location"]).query)["state"][0]

    with patch(
        "copilot.server.discover_smart_endpoints",
        new_callable=AsyncMock,
        return_value={
            "authorization_endpoint": "https://openemr.example/oauth2/default/authorize",
            "token_endpoint": "https://openemr.example/oauth2/default/token",
        },
    ), patch(
        "copilot.server.exchange_code_for_token",
        new_callable=AsyncMock,
        return_value=_mock_token_response(),
    ):
        callback_resp = auth_client.get(
            f"/auth/smart/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )

    session_cookie = callback_resp.cookies.get("copilot_session")

    # Logout
    logout_resp = auth_client.post(
        "/auth/logout", cookies={"copilot_session": session_cookie}
    )
    assert logout_resp.status_code == 200

    # /me should now 401
    me_resp = auth_client.get("/me", cookies={"copilot_session": session_cookie})
    assert me_resp.status_code == 401


# ---------- GET /panel ----------


async def _seed_session(client: TestClient, fhir_user: str) -> str:
    """Mint a session row directly via the gateway so /panel tests don't have
    to walk the full OAuth flow with a mock id_token. Returns the cookie value.
    """
    import time

    from copilot import server as server_mod
    from copilot.session import SessionRow

    gateway = server_mod.app.state.session_gateway
    session_id = "test-session-" + fhir_user.replace("/", "-")
    now = time.time()
    await gateway.create_session(
        SessionRow(
            session_id=session_id,
            oe_user_id=42,
            display_name="Test User",
            fhir_user=fhir_user,
            created_at=now,
            expires_at=now + 3600,
        )
    )
    return session_id


def test_panel_returns_401_without_cookie(auth_client: TestClient) -> None:
    resp = auth_client.get("/panel")
    assert resp.status_code == 401


async def test_panel_returns_dr_smith_subset(auth_client: TestClient) -> None:
    """A session whose fhirUser is dr_smith sees the three patients on his
    CareTeam. fixture-2 (Maya) and fixture-4 (Linda) are filtered out."""
    cookie = await _seed_session(
        auth_client, "Practitioner/practitioner-dr-smith"
    )

    resp = auth_client.get("/panel", cookies={"copilot_session": cookie})
    assert resp.status_code == 200
    body = resp.json()
    pids = sorted(p["patient_id"] for p in body["patients"])
    assert pids == ["fixture-1", "fixture-3", "fixture-5"]
    eduardo = next(p for p in body["patients"] if p["patient_id"] == "fixture-1")
    assert eduardo["family_name"] == "Perez"
    assert eduardo["given_name"] == "Eduardo"
    assert eduardo["birth_date"] == "1958-03-12"


async def test_panel_admin_sees_full_set(
    auth_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Admin user_id (in the configured allow-list) bypasses the gate."""
    admin_practitioner = "practitioner-admin"
    monkeypatch.setenv("COPILOT_ADMIN_USER_IDS", admin_practitioner)

    # Re-read settings on the app so the new env var lands.
    from copilot import server as server_mod
    from copilot.config import Settings

    server_mod.app.state.settings = Settings()

    cookie = await _seed_session(
        auth_client, f"Practitioner/{admin_practitioner}"
    )

    resp = auth_client.get("/panel", cookies={"copilot_session": cookie})
    assert resp.status_code == 200
    body = resp.json()
    pids = sorted(p["patient_id"] for p in body["patients"])
    assert pids == ["fixture-1", "fixture-2", "fixture-3", "fixture-4", "fixture-5"]
