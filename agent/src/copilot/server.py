"""FastAPI entry point.

Exposes the chat surface (POST /chat), a health check, SMART EHR launch
endpoints, and standalone auth endpoints (``/auth/*``).

Wire shapes (request and response) are defined in :mod:`copilot.api.schemas`
and mirror ``agentforge-docs/CHAT-API-CONTRACT.md``.
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlencode

from fastapi import Cookie, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from .api.schemas import (
    Block,
    ChatRequest,
    ChatResponse,
    OvernightBlock,
    PlainBlock,
    TriageBlock,
)
from .care_team import CareTeamGate
from .checkpointer import open_checkpointer
from .config import get_settings
from .fhir import FhirClient
from .graph import build_graph
from .observability import get_callback_handler
from .session import (
    InMemorySessionStore,
    LaunchStateRow,
    SessionGateway,
    SessionRow,
    TokenBundleRow,
    open_session_store,
    parse_fhir_user,
)
from .smart import (
    LaunchState,
    build_authorize_redirect_url,
    code_challenge_for,
    discover_smart_endpoints,
    exchange_code_for_token,
    generate_code_verifier,
    generate_state,
    get_default_stores,
    token_bundle_from_response,
)

_log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    # open_checkpointer falls back to MemorySaver when CHECKPOINTER_DSN is
    # unset, so this same path serves dev (no DSN) and production (Postgres).
    async with open_checkpointer(settings) as checkpointer:
        app.state.graph = build_graph(settings, checkpointer=checkpointer)
        # Session gateway for standalone auth: Postgres-backed when DSN is
        # set, in-memory otherwise. Tests inject their own gateway before
        # entering the lifespan, so respect a pre-existing one.
        if hasattr(app.state, "session_gateway"):
            yield
            return
        if settings.checkpointer_dsn:
            async with open_session_store(settings.checkpointer_dsn) as store:
                app.state.session_gateway = SessionGateway(store=store)
                yield
        else:
            app.state.session_gateway = SessionGateway(store=InMemorySessionStore())
            yield


app = FastAPI(title="OpenEMR Clinical Co-Pilot", version="0.1.0", lifespan=lifespan)


def _resolve_allowed_origins() -> list[str]:
    """Read CORS allow-list from settings, defaulting to the local Vite port."""

    settings = get_settings()
    if settings.allowed_origins:
        return list(settings.allowed_origins)
    return ["http://localhost:5173"]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_resolve_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _assert_patient_context_matches(
    conversation_id: str, patient_id: str
) -> None:
    """Defense-in-depth check above the tool layer.

    When a ``TokenBundle`` is bound to ``conversation_id`` (i.e. the SMART
    launch completed), reject any /chat call whose ``patient_id`` doesn't
    match the launched context with HTTP 403. The tool layer also enforces
    this per ARCHITECTURE.md §7, but bouncing it at the API boundary keeps
    a malicious or buggy client from triggering a graph invocation that
    spends tokens before the guard fires.

    No-op when there is no bound bundle (dev/test paths that pass
    ``patient_id`` directly in the request body).
    """

    bundle = get_default_stores().get_token(conversation_id)
    if bundle is None:
        return None
    if bundle.patient_id and patient_id and bundle.patient_id != patient_id:
        raise HTTPException(
            status_code=403,
            detail=(
                "patient_context_mismatch: launched session is bound to a "
                "different patient — re-launch the Co-Pilot from the chart"
            ),
        )
    return None


def _coerce_block_dict(block_dict: dict[str, Any] | None, fallback_text: str) -> Block:
    """Convert a state-dict block payload back into a typed Block.

    The graph stores blocks as plain dicts in state for serialization
    safety. Validation here re-enforces the wire schema before FastAPI
    serializes it.
    """

    if not block_dict:
        _log.warning("graph returned no block; falling back to PlainBlock")
        return PlainBlock(lead=fallback_text or "(no response)")
    kind = block_dict.get("kind")
    try:
        if kind == "triage":
            return TriageBlock.model_validate(block_dict)
        if kind == "overnight":
            return OvernightBlock.model_validate(block_dict)
        return PlainBlock.model_validate(block_dict)
    except ValidationError as exc:
        _log.warning(
            "block validation failed (kind=%s): %s; falling back to PlainBlock",
            kind,
            exc,
        )
        return PlainBlock(lead=fallback_text or "(no response)")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse, response_model_by_alias=True)
async def chat(req: ChatRequest) -> ChatResponse:
    graph = app.state.graph
    settings = app.state.settings

    # Resolve SMART context from stores when the body doesn't carry it.
    stores = get_default_stores()
    bundle = stores.get_token(req.conversation_id)
    patient_id = req.patient_id or (bundle.patient_id if bundle else "")
    user_id = req.user_id or (bundle.user_id if bundle else "")
    smart_access_token = req.smart_access_token or (bundle.access_token if bundle else "")

    if not patient_id:
        # The session is unbound: either the launch expired or no launch
        # ever happened and the body didn't carry the context (dev only).
        raise HTTPException(
            status_code=401,
            detail=(
                "session has no bound patient context — re-launch the Co-Pilot "
                "from the patient's chart"
            ),
        )

    _assert_patient_context_matches(req.conversation_id, patient_id)

    config: dict[str, Any] = {"configurable": {"thread_id": req.conversation_id}}
    handler = get_callback_handler(settings)
    if handler is not None:
        config["callbacks"] = [handler]
    inputs = {
        "messages": [HumanMessage(content=req.message)],
        "conversation_id": req.conversation_id,
        "patient_id": patient_id,
        "user_id": user_id,
        "smart_access_token": smart_access_token,
    }
    result = await graph.ainvoke(inputs, config=config)
    messages = result.get("messages") or []
    if not messages:
        raise HTTPException(status_code=500, detail="graph returned no messages")
    reply_raw = messages[-1].content
    reply = reply_raw if isinstance(reply_raw, str) else str(reply_raw)

    block = _coerce_block_dict(result.get("block"), fallback_text=reply)
    # Frontend reads block.lead for the typewriter; reply is duplicated for
    # plain-text consumers (logs, smoke tests) per the contract.
    return ChatResponse(
        conversation_id=req.conversation_id,
        reply=block.lead,
        block=block,
        state={
            "patient_id": result.get("patient_id"),
            "workflow_id": result.get("workflow_id"),
            "classifier_confidence": float(result.get("classifier_confidence") or 0.0),
            "message_count": len(messages),
        },
    )


@app.get("/smart/launch")
async def smart_launch(iss: str = "", launch: str = "") -> RedirectResponse:
    """SMART EHR launch entry point.

    The EHR redirects the user here with two query params:
    ``iss`` — the FHIR base URL of the EHR (e.g. https://openemr.example/apis/default/fhir).
    ``launch`` — opaque launch context token issued by the EHR.

    We:
    1. Discover the EHR's authorize+token endpoints via ``.well-known/smart-configuration``.
    2. Generate a PKCE verifier+challenge and a state nonce.
    3. Stash {state: launch context} so /smart/callback can recover it.
    4. 302 the user to the EHR's authorize endpoint.
    """
    if not iss or not launch:
        raise HTTPException(status_code=400, detail="missing iss or launch query param")
    settings = app.state.settings
    if not settings.smart_client_id:
        raise HTTPException(
            status_code=503,
            detail="SMART_CLIENT_ID not configured; register the Co-Pilot in OpenEMR first",
        )

    config = await discover_smart_endpoints(iss)
    authorization_endpoint = config.get("authorization_endpoint")
    if not authorization_endpoint:
        raise HTTPException(
            status_code=502,
            detail=f"could not resolve authorization_endpoint from {iss}",
        )

    code_verifier = generate_code_verifier()
    state = generate_state()

    stores = get_default_stores()
    stores.put_launch_state(
        state,
        LaunchState(
            iss=iss,
            launch=launch,
            code_verifier=code_verifier,
            issued_at=time.time(),
        ),
    )

    redirect_url = build_authorize_redirect_url(
        settings=settings,
        iss=iss,
        launch=launch,
        authorization_endpoint=authorization_endpoint,
        state=state,
        code_challenge=code_challenge_for(code_verifier),
    )
    return RedirectResponse(url=redirect_url, status_code=302)


@app.get("/smart/callback", response_model=None)
async def smart_callback(
    code: str = "", state: str = "", error: str = ""
) -> RedirectResponse | dict[str, Any]:
    """OAuth2 redirect target.

    The EHR redirects back here with ``code`` (the authorization code) plus
    the ``state`` we issued during /smart/launch. We exchange the code for
    an access_token at the EHR's token endpoint, bind the resulting bundle
    to a fresh conversation_id, and 302 the user to the chat UI with the
    conversation context in the query string. When ``COPILOT_UI_URL`` is
    not configured we fall back to returning the bundle as JSON so the dev
    flow still works without a deployed frontend.
    """
    if error:
        raise HTTPException(status_code=400, detail=f"authorization error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code or state")

    stores = get_default_stores()
    launch_state = stores.pop_launch_state(state)
    if launch_state is None:
        raise HTTPException(
            status_code=400,
            detail="unknown or expired state — re-launch the Co-Pilot from the chart",
        )

    settings = app.state.settings
    config = await discover_smart_endpoints(launch_state.iss)
    token_endpoint = config.get("token_endpoint")
    if not token_endpoint:
        raise HTTPException(status_code=502, detail="could not resolve token_endpoint")

    try:
        payload = await exchange_code_for_token(
            settings=settings,
            token_endpoint=token_endpoint,
            code=code,
            code_verifier=launch_state.code_verifier,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"token exchange failed: {exc}") from exc

    bundle = token_bundle_from_response(payload, iss=launch_state.iss)
    if not bundle.access_token:
        raise HTTPException(status_code=502, detail="token endpoint returned no access_token")
    if not bundle.patient_id:
        raise HTTPException(
            status_code=502,
            detail="token endpoint did not return a patient context (launch token may be invalid)",
        )

    conversation_id = secrets.token_urlsafe(16)
    stores.put_token(conversation_id, bundle)

    if not settings.copilot_ui_url:
        return {
            "conversation_id": conversation_id,
            "patient_id": bundle.patient_id,
            "user_id": bundle.user_id,
            "scope": bundle.scope,
            "expires_in": bundle.expires_in,
            "iss": bundle.iss,
        }

    params = urlencode(
        {
            "conversation_id": conversation_id,
            "patient": bundle.patient_id,
            "user": bundle.user_id,
            "iss": bundle.iss,
            "scope": bundle.scope,
            "expires_in": bundle.expires_in,
        }
    )
    return RedirectResponse(
        url=f"{settings.copilot_ui_url.rstrip('/')}/?{params}",
        status_code=302,
    )


# ---------------------------------------------------------------------------
# Standalone auth endpoints
# ---------------------------------------------------------------------------

STANDALONE_LAUNCH_STATE_TTL = 600  # 10 minutes
SESSION_COOKIE_NAME = "copilot_session"


def _parse_id_token_claims(id_token: str) -> dict[str, Any]:
    """Decode the payload of a JWT id_token without verifying the signature.

    We trust the token because it was received over a direct HTTPS POST to
    the token endpoint; the TLS channel provides authenticity. Signature
    verification is a defense-in-depth improvement tracked separately.
    """
    parts = id_token.split(".")
    if len(parts) < 2:
        return {}
    # Add padding — JWT base64url omits trailing '='.
    payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return {}


@app.get("/auth/login")
async def auth_login() -> RedirectResponse:
    """Initiate SMART standalone login.

    Generates PKCE verifier+challenge and state, persists launch state, and
    302s the user to the OpenEMR authorize endpoint. No ``iss`` or ``launch``
    params — standalone flow uses the configured FHIR base directly.
    """
    settings = app.state.settings
    if not settings.smart_standalone_client_id:
        raise HTTPException(
            status_code=503,
            detail=(
                "SMART_STANDALONE_CLIENT_ID not configured — "
                "register the copilot-standalone client in OpenEMR first"
            ),
        )

    iss = settings.openemr_fhir_base
    config = await discover_smart_endpoints(iss)
    authorization_endpoint = config.get("authorization_endpoint")
    if not authorization_endpoint:
        raise HTTPException(
            status_code=502,
            detail=f"could not resolve authorization_endpoint from {iss}",
        )

    code_verifier = generate_code_verifier()
    state = generate_state()
    now = time.time()

    gateway: SessionGateway = app.state.session_gateway
    await gateway.create_launch_state(
        LaunchStateRow(
            state=state,
            code_verifier=code_verifier,
            redirect_uri=settings.smart_standalone_redirect_uri,
            expires_at=now + STANDALONE_LAUNCH_STATE_TTL,
        )
    )

    params = {
        "response_type": "code",
        "client_id": settings.smart_standalone_client_id,
        "redirect_uri": settings.smart_standalone_redirect_uri,
        "scope": settings.smart_standalone_scopes,
        "state": state,
        "aud": iss,
        "code_challenge": code_challenge_for(code_verifier),
        "code_challenge_method": "S256",
    }
    redirect_url = f"{authorization_endpoint}?{urlencode(params)}"
    return RedirectResponse(url=redirect_url, status_code=302)


@app.get("/auth/smart/callback", response_model=None)
async def auth_standalone_callback(
    code: str = "", state: str = "", error: str = ""
) -> RedirectResponse | dict[str, Any]:
    """OAuth2 callback for the standalone login flow.

    Exchanges the authorization code for tokens, parses the ``fhirUser``
    claim from the id_token, mints a session, stores the token bundle,
    sets an HttpOnly session cookie, and 302s to the copilot-ui root.
    """
    if error:
        raise HTTPException(status_code=400, detail=f"authorization error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="missing code or state")

    gateway: SessionGateway = app.state.session_gateway
    launch_state = await gateway.pop_launch_state(state)
    if launch_state is None:
        raise HTTPException(
            status_code=400,
            detail="unknown or expired state — please log in again",
        )

    settings = app.state.settings
    iss = settings.openemr_fhir_base
    config = await discover_smart_endpoints(iss)
    token_endpoint = config.get("token_endpoint")
    if not token_endpoint:
        raise HTTPException(status_code=502, detail="could not resolve token_endpoint")

    try:
        payload = await exchange_code_for_token(
            settings=settings,
            token_endpoint=token_endpoint,
            code=code,
            code_verifier=launch_state.code_verifier,
            client_id_override=settings.smart_standalone_client_id,
            client_secret_override=settings.smart_standalone_client_secret.get_secret_value(),
            redirect_uri_override=settings.smart_standalone_redirect_uri,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"token exchange failed: {exc}"
        ) from exc

    access_token = payload.get("access_token", "")
    if not access_token:
        raise HTTPException(
            status_code=502, detail="token endpoint returned no access_token"
        )

    # Parse fhirUser from id_token claims.
    id_token = payload.get("id_token", "")
    claims = _parse_id_token_claims(id_token) if id_token else {}
    fhir_user = claims.get("fhirUser", "")
    display_name = claims.get("name", "") or str(payload.get("user", ""))

    now = time.time()
    session_id = secrets.token_urlsafe(32)
    session = SessionRow(
        session_id=session_id,
        oe_user_id=0,  # resolved via fhirUser → users.uuid lookup in a future pass
        display_name=display_name,
        fhir_user=fhir_user,
        created_at=now,
        expires_at=now + settings.session_ttl_seconds,
    )
    await gateway.create_session(session)

    token_bundle = TokenBundleRow(
        session_id=session_id,
        access_token=access_token,
        refresh_token=payload.get("refresh_token", ""),
        id_token=id_token,
        scope=payload.get("scope", ""),
        issuer=iss,
        expires_at=now + int(payload.get("expires_in", 3600)),
    )
    await gateway.upsert_token_bundle(token_bundle)

    # Build the redirect response with the session cookie.
    ui_url = settings.copilot_ui_url or "http://localhost:5173"
    response = RedirectResponse(url=ui_url, status_code=302)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        secure=False,  # False for localhost dev; True in production via reverse proxy
        samesite="lax",
        path="/",
        max_age=settings.session_ttl_seconds,
    )
    return response


@app.get("/me")
async def me(
    copilot_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Return the authenticated user's info, or 401 if no valid session."""
    if not copilot_session:
        raise HTTPException(status_code=401, detail="not authenticated")

    gateway: SessionGateway = app.state.session_gateway
    session = await gateway.get_session(copilot_session)
    if session is None:
        raise HTTPException(status_code=401, detail="session expired or invalid")

    return {
        "user_id": session.oe_user_id,
        "display_name": session.display_name,
        "fhir_user": session.fhir_user,
    }


@app.get("/panel")
async def panel(
    copilot_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Return the authenticated user's CareTeam roster.

    Drives the empty-state Panel UI in the standalone shell. The roster is
    scoped to whatever ``CareTeamGate.list_panel`` returns: dr_smith sees a
    subset, admin (via the configured allow-list) sees the full set.

    The user is identified via the session cookie's ``fhir_user`` claim
    (a ``Practitioner/<uuid>`` reference); we extract the uuid and pass it
    to the gate. When the session has no fhirUser (e.g. legacy/dev), the
    gate gets an empty user_id and returns an empty panel.
    """
    if not copilot_session:
        raise HTTPException(status_code=401, detail="not authenticated")

    gateway: SessionGateway = app.state.session_gateway
    session = await gateway.get_session(copilot_session)
    if session is None:
        raise HTTPException(status_code=401, detail="session expired or invalid")

    settings = app.state.settings
    _, practitioner_id = parse_fhir_user(session.fhir_user)
    gate = CareTeamGate(
        FhirClient(settings),
        admin_user_ids=frozenset(settings.admin_user_ids),
    )
    panel = await gate.list_panel(practitioner_id)
    return {
        "user_id": session.oe_user_id,
        "patients": [
            {
                "patient_id": p.patient_id,
                "given_name": p.given_name,
                "family_name": p.family_name,
                "birth_date": p.birth_date,
                "last_admission": p.last_admission,
                "room": p.room,
            }
            for p in panel
        ],
    }


@app.post("/auth/logout")
async def auth_logout(
    response: Response,
    copilot_session: str | None = Cookie(default=None),
) -> dict[str, str]:
    """Revoke the session and clear the cookie."""
    if copilot_session:
        gateway: SessionGateway = app.state.session_gateway
        await gateway.delete_session(copilot_session)

    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"status": "logged_out"}
