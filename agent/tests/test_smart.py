"""SMART on FHIR EHR launch helpers — unit-level coverage.

Exercises PKCE generation, well-known discovery (mocked), token exchange
(mocked), and the in-memory state/token stores. The full /smart/launch +
/smart/callback dance is covered by the integration story in
``agentforge-docs/AGENT-TODO.md`` (E2E smoke task) — those need a registered
client and a live OpenEMR.
"""

from __future__ import annotations

import base64
import hashlib

import httpx
import pytest

from copilot.config import Settings
from copilot.smart import (
    LaunchState,
    SmartStores,
    TokenBundle,
    build_authorize_redirect_url,
    code_challenge_for,
    discover_smart_endpoints,
    exchange_code_for_token,
    generate_code_verifier,
    generate_state,
    token_bundle_from_response,
)


def _settings() -> Settings:
    return Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        SMART_CLIENT_ID="copilot-test-client",
        SMART_CLIENT_SECRET="hunter2",
        SMART_REDIRECT_URI="https://copilot.example/smart/callback",
        SMART_SCOPES="launch openid fhirUser patient/*.read",
    )


def test_pkce_verifier_and_challenge_match_rfc7636() -> None:
    verifier = generate_code_verifier()
    challenge = code_challenge_for(verifier)

    expected = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert challenge == expected
    # No padding, URL-safe
    assert "=" not in challenge
    assert "+" not in challenge and "/" not in challenge


def test_state_is_unique_per_call() -> None:
    seen = {generate_state() for _ in range(50)}
    assert len(seen) == 50


def test_authorize_redirect_url_contains_required_params() -> None:
    settings = _settings()
    url = build_authorize_redirect_url(
        settings=settings,
        iss="https://openemr.example/apis/default/fhir",
        launch="lt-abc",
        authorization_endpoint="https://openemr.example/oauth2/default/authorize",
        state="state-xyz",
        code_challenge="ch-1",
    )
    assert url.startswith("https://openemr.example/oauth2/default/authorize?")
    for needle in [
        "response_type=code",
        f"client_id={settings.smart_client_id}",
        "code_challenge=ch-1",
        "code_challenge_method=S256",
        "state=state-xyz",
        "launch=lt-abc",
    ]:
        assert needle in url, f"missing {needle!r} in redirect URL"


def test_smart_stores_state_round_trip_and_eviction() -> None:
    stores = SmartStores()
    ls = LaunchState(
        iss="https://openemr.example/apis/default/fhir",
        launch="lt-1",
        code_verifier="v",
        issued_at=0.0,  # ancient → swept on next access
    )
    stores.put_launch_state("state-1", ls)
    fresh = LaunchState(
        iss="https://openemr.example/apis/default/fhir",
        launch="lt-2",
        code_verifier="v2",
        issued_at=__import__("time").time(),
    )
    stores.put_launch_state("state-2", fresh)

    # The aged entry was swept by the put_launch_state call.
    assert stores.pop_launch_state("state-1") is None
    assert stores.pop_launch_state("state-2") is not None
    # Pop is one-shot.
    assert stores.pop_launch_state("state-2") is None


def test_smart_stores_token_expiry() -> None:
    stores = SmartStores()
    bundle = TokenBundle(
        access_token="at",
        refresh_token="",
        id_token="",
        scope="patient/*.read",
        patient_id="4",
        user_id="dr_lopez",
        iss="https://openemr.example/apis/default/fhir",
        issued_at=0.0,
        expires_in=10,  # already expired
    )
    stores.put_token("conv-1", bundle)
    assert stores.get_token("conv-1") is None  # expired

    # Use expires_in well above the 30s slack so the token is "live".
    fresh = TokenBundle(
        **{**bundle.__dict__, "issued_at": __import__("time").time(), "expires_in": 3600}
    )
    stores.put_token("conv-2", fresh)
    assert stores.get_token("conv-2") is fresh


async def test_discover_smart_endpoints_falls_back_when_well_known_missing() -> None:
    """OpenEMR sometimes doesn't publish the well-known doc; the fallback
    constructs the canonical OpenEMR authorize/token URLs directly.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        config = await discover_smart_endpoints(
            "https://openemr.example/apis/default/fhir", client=client
        )
    assert config["_fallback"] is True
    assert config["authorization_endpoint"].endswith("/authorize")
    assert config["token_endpoint"].endswith("/token")


async def test_discover_smart_endpoints_uses_published_doc() -> None:
    expected = {
        "authorization_endpoint": "https://openemr.example/auth/code",
        "token_endpoint": "https://openemr.example/auth/token",
        "issuer": "https://openemr.example",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/.well-known/smart-configuration")
        return httpx.Response(200, json=expected)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        config = await discover_smart_endpoints(
            "https://openemr.example/apis/default/fhir", client=client
        )
    assert config == expected


async def test_exchange_code_for_token_posts_expected_payload() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        for kv in request.content.decode().split("&"):
            k, _, v = kv.partition("=")
            captured[k] = httpx._utils.unquote(v) if hasattr(httpx, "_utils") else v.replace("%2F", "/")
        return httpx.Response(
            200,
            json={
                "access_token": "at-xyz",
                "refresh_token": "rt-abc",
                "id_token": "id.jwt.value",
                "scope": "launch patient/*.read",
                "expires_in": 3600,
                "patient": "4",
                "sub": "dr_lopez",
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        payload = await exchange_code_for_token(
            settings=_settings(),
            token_endpoint="https://openemr.example/oauth2/default/token",
            code="auth-code-1",
            code_verifier="verifier-1",
            client=client,
        )

    assert payload["access_token"] == "at-xyz"
    assert captured["grant_type"] == "authorization_code"
    assert captured["code"] == "auth-code-1"
    assert captured["code_verifier"] == "verifier-1"
    assert captured["client_id"] == "copilot-test-client"
    assert captured["client_secret"] == "hunter2"


async def test_exchange_code_raises_on_non_200() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(RuntimeError) as excinfo:
            await exchange_code_for_token(
                settings=_settings(),
                token_endpoint="https://openemr.example/oauth2/default/token",
                code="x",
                code_verifier="y",
                client=client,
            )
    assert "401" in str(excinfo.value)


def test_token_bundle_from_response_parses_known_fields() -> None:
    payload = {
        "access_token": "at-xyz",
        "refresh_token": "rt-abc",
        "id_token": "id.jwt.value",
        "scope": "launch patient/*.read",
        "expires_in": 3600,
        "patient": "4",
        "sub": "dr_lopez",
    }
    bundle = token_bundle_from_response(payload, iss="https://openemr.example/apis/default/fhir")
    assert bundle.access_token == "at-xyz"
    assert bundle.refresh_token == "rt-abc"
    assert bundle.patient_id == "4"
    assert bundle.user_id == "dr_lopez"
    assert bundle.scope == "launch patient/*.read"
    assert bundle.expires_in == 3600
    assert bundle.expired() is False
