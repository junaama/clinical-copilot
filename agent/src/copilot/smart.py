"""SMART on FHIR EHR launch helpers.

Implements the SMART App Launch flow (HL7 spec) for the EHR-launch shape:

    EHR ──(iss, launch)──▶  /smart/launch
                             │ discover .well-known/smart-configuration
                             │ generate PKCE verifier + challenge
                             │ stash launch_state[state] = {iss, verifier, launch}
                             ▼
    302 redirect to authorize_endpoint?response_type=code&client_id=…&launch=…&aud=…&state=…&code_challenge=…

    EHR ──(code, state)─────▶  /smart/callback
                             │ pop launch_state[state]
                             │ POST token_endpoint  (code + verifier + client_secret)
                             ▼
    response: {access_token, refresh_token?, expires_in, scope, patient}
    stash tokens[conversation_id] = TokenBundle  (in-memory; Redis-roadmap)

Token storage is process-local for week 1 — single-replica only. Multi-replica
deployment requires moving the stores into Redis or Postgres (tracked in
``agentforge-docs/AGENT-TODO.md``).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from .config import Settings

_log = logging.getLogger(__name__)

LAUNCH_STATE_TTL_SECONDS = 600  # 10 min
TOKEN_TTL_SLACK_SECONDS = 30    # consider tokens expired this many seconds early


@dataclass(frozen=True)
class LaunchState:
    """Per-launch ephemeral context held while the user authenticates with the EHR."""

    iss: str
    launch: str
    code_verifier: str
    issued_at: float
    redirect_back_to: str = ""


@dataclass(frozen=True)
class TokenBundle:
    """Result of a successful authorization-code exchange."""

    access_token: str
    refresh_token: str
    id_token: str
    scope: str
    patient_id: str
    user_id: str
    iss: str
    issued_at: float
    expires_in: int

    def expired(self, now: float | None = None) -> bool:
        now = now or time.time()
        return (now - self.issued_at) >= max(0, self.expires_in - TOKEN_TTL_SLACK_SECONDS)


@dataclass
class SmartStores:
    """In-memory stores. Process-local. Replace with Redis/Postgres for multi-replica."""

    launch_state: dict[str, LaunchState] = field(default_factory=dict)
    tokens_by_conversation: dict[str, TokenBundle] = field(default_factory=dict)

    def put_launch_state(self, state: str, ls: LaunchState) -> None:
        self._sweep_launch_state()
        self.launch_state[state] = ls

    def pop_launch_state(self, state: str) -> LaunchState | None:
        self._sweep_launch_state()
        return self.launch_state.pop(state, None)

    def put_token(self, conversation_id: str, tb: TokenBundle) -> None:
        self.tokens_by_conversation[conversation_id] = tb

    def get_token(self, conversation_id: str) -> TokenBundle | None:
        tb = self.tokens_by_conversation.get(conversation_id)
        if tb is None or tb.expired():
            return None
        return tb

    def _sweep_launch_state(self) -> None:
        now = time.time()
        stale = [s for s, ls in self.launch_state.items()
                 if now - ls.issued_at > LAUNCH_STATE_TTL_SECONDS]
        for s in stale:
            self.launch_state.pop(s, None)


# Module-level singleton for the FastAPI app to share. Tests use a fresh
# instance via ``SmartStores()``.
_default_stores = SmartStores()


def get_default_stores() -> SmartStores:
    return _default_stores


# ----- PKCE helpers (RFC 7636) -----

def generate_code_verifier() -> str:
    """43–128 chars of URL-safe base64 entropy. We use 64 bytes → 86 chars."""
    return secrets.token_urlsafe(64)


def code_challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def generate_state() -> str:
    return secrets.token_urlsafe(32)


# ----- SMART well-known discovery -----

async def discover_smart_endpoints(
    iss: str, *, client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    """Fetch ``<iss>/.well-known/smart-configuration``.

    Per the SMART spec: ``authorization_endpoint`` and ``token_endpoint`` are
    required fields. Falls back to OpenEMR's known path if discovery fails
    (OpenEMR doesn't always publish the well-known doc on older builds).
    """
    url = iss.rstrip("/") + "/.well-known/smart-configuration"
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await client.get(url, headers={"Accept": "application/json"})
        if response.status_code == 200:
            return response.json()
    except httpx.HTTPError as exc:
        _log.warning("smart-configuration discovery failed for %s: %s", iss, exc)
    finally:
        if own_client:
            await client.aclose()

    # OpenEMR fallback (matches src/RestControllers/AuthorizationController.php).
    base = iss.rstrip("/").removesuffix("/fhir")
    return {
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "issuer": iss,
        "_fallback": True,
    }


# ----- /smart/launch helpers -----

def build_authorize_redirect_url(
    *,
    settings: Settings,
    iss: str,
    launch: str,
    authorization_endpoint: str,
    state: str,
    code_challenge: str,
) -> str:
    """Construct the authorize-endpoint URL the EHR will redirect the user to."""
    params = {
        "response_type": "code",
        "client_id": settings.smart_client_id,
        "redirect_uri": settings.smart_redirect_uri,
        "scope": settings.smart_scopes,
        "state": state,
        "aud": iss,
        "launch": launch,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{authorization_endpoint}?{urlencode(params)}"


# ----- /smart/callback helpers -----

async def exchange_code_for_token(
    *,
    settings: Settings,
    token_endpoint: str,
    code: str,
    code_verifier: str,
    client: httpx.AsyncClient | None = None,
    client_id_override: str = "",
    client_secret_override: str = "",
    redirect_uri_override: str = "",
) -> dict[str, Any]:
    """Exchange the authorization code for an access token.

    Optional ``*_override`` params let the standalone flow use different
    client credentials than the EHR-launch flow without duplicating logic.
    """
    client_id = client_id_override or settings.smart_client_id
    client_secret = client_secret_override or settings.smart_client_secret.get_secret_value()
    redirect_uri = redirect_uri_override or settings.smart_redirect_uri

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    if client_secret:
        payload["client_secret"] = client_secret

    own_client = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await client.post(
            token_endpoint,
            data=payload,
            headers={"Accept": "application/json"},
        )
    finally:
        if own_client:
            await client.aclose()

    if response.status_code != 200:
        raise RuntimeError(
            f"token endpoint returned {response.status_code}: {response.text[:200]}"
        )
    return response.json()


async def refresh_access_token(
    *,
    token_endpoint: str,
    refresh_token: str,
    client_id: str,
    client_secret: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Exchange a refresh token for a fresh access token.

    Mirrors :func:`exchange_code_for_token` but uses the
    ``grant_type=refresh_token`` grant. Returns the raw token-endpoint JSON
    so callers can persist whatever shape the server returns (some servers
    rotate ``refresh_token``, some don't; some return ``id_token``, some
    don't).

    The client credentials are passed explicitly so the caller picks the
    right pair (EHR-launch vs. standalone) without the helper reaching into
    settings.
    """
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if client_secret:
        payload["client_secret"] = client_secret

    own_client = client is None
    client = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await client.post(
            token_endpoint,
            data=payload,
            headers={"Accept": "application/json"},
        )
    finally:
        if own_client:
            await client.aclose()

    if response.status_code != 200:
        raise RuntimeError(
            f"token endpoint refused refresh ({response.status_code}): "
            f"{response.text[:200]}"
        )
    return response.json()


def token_bundle_from_response(payload: dict[str, Any], iss: str) -> TokenBundle:
    """Parse a token-endpoint JSON response into a ``TokenBundle``."""
    return TokenBundle(
        access_token=payload.get("access_token", ""),
        refresh_token=payload.get("refresh_token", ""),
        id_token=payload.get("id_token", ""),
        scope=payload.get("scope", ""),
        patient_id=str(payload.get("patient", "")),
        user_id=str(payload.get("user", "") or payload.get("sub", "")),
        iss=iss,
        issued_at=time.time(),
        expires_in=int(payload.get("expires_in", 3600)),
    )
