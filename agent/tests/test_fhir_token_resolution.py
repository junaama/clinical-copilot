"""FHIR client token resolution.

The active SMART token (set per-turn via the contextvar) takes precedence
over the static ``OPENEMR_FHIR_TOKEN`` env. Fixture mode wins over both
when ``USE_FIXTURE_FHIR=1``.
"""

from __future__ import annotations

import httpx
import pytest

from copilot.config import Settings
from copilot.fhir import FhirClient
from copilot.tools import set_active_smart_token


@pytest.fixture(autouse=True)
def _reset_token():
    set_active_smart_token(None)
    yield
    set_active_smart_token(None)


def test_fixture_mode_wins_when_use_fixture_fhir_set() -> None:
    s = Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        USE_FIXTURE_FHIR=True,
        OPENEMR_FHIR_TOKEN="static-token",
    )
    client = FhirClient(s)
    set_active_smart_token("smart-token")
    assert client.fixture_mode is True


def test_smart_token_overrides_static_env_token() -> None:
    s = Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        USE_FIXTURE_FHIR=False,
        OPENEMR_FHIR_TOKEN="static-token",
    )
    client = FhirClient(s)

    set_active_smart_token("smart-token-from-launch")

    captured: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"resourceType": "Patient", "id": "fixture-1"})

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    import asyncio
    asyncio.run(client.read("Patient", "fixture-1"))

    assert captured["authorization"] == "Bearer smart-token-from-launch"


def test_falls_back_to_static_token_when_no_smart_context() -> None:
    s = Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        USE_FIXTURE_FHIR=False,
        OPENEMR_FHIR_TOKEN="static-fallback",
    )
    client = FhirClient(s)

    captured: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"entry": []})

    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    import asyncio
    asyncio.run(client.search("Observation", {"patient": "fixture-1"}))

    assert captured["authorization"] == "Bearer static-fallback"


def test_no_implicit_fixture_fallback_in_production() -> None:
    """When ``USE_FIXTURE_FHIR=0`` and no token is resolvable, the client
    must NOT silently serve fixture data — production deploys would otherwise
    fabricate clinical content from the synthetic bundle. ``search`` and
    ``read`` surface ``error='no_token'`` instead.
    """
    s = Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        USE_FIXTURE_FHIR=False,
        OPENEMR_FHIR_TOKEN="",
    )
    client = FhirClient(s)
    assert client.fixture_mode is False

    import asyncio
    ok, entries, err, _ = asyncio.run(client.search("Patient", {"_id": "x"}))
    assert ok is False
    assert entries == []
    assert err == "no_token"

    ok, resource, err, _ = asyncio.run(client.read("Patient", "x"))
    assert ok is False
    assert resource is None
    assert err == "no_token"
