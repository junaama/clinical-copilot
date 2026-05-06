"""FastAPI entry point.

Exposes the chat surface (POST /chat), a health check, SMART EHR launch
endpoints, and standalone auth endpoints (``/auth/*``).

Wire shapes (request and response) are defined in :mod:`copilot.api.schemas`
and mirror ``agentforge-docs/CHAT-API-CONTRACT.md``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import (
    BackgroundTasks,
    Cookie,
    FastAPI,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ValidationError

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
from .conversations import (
    ConversationRegistry,
    InMemoryConversationStore,
    open_conversation_store,
)
from .extraction.bbox_matcher import match_extraction_to_bboxes
from .extraction.document_client import UPLOAD_LANDED_ID_LOST, DocumentClient
from .extraction.persistence import DocumentExtractionStore
from .extraction.schemas import IntakeExtraction, LabExtraction
from .extraction.type_guard import detect_doc_type
from .extraction.vlm import extract_document as _vlm_extract_document
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
    refresh_access_token,
    token_bundle_from_response,
)
from .supervisor.upload import build_document_upload_message
from .title_summarizer import (
    HaikuTitleSummarizer,
    build_default_haiku_factory,
)
from .token_crypto import TokenEncryptor, load_encryptor_from_env
from .tools import set_active_smart_token

_log = logging.getLogger(__name__)


def _load_token_encryptor(settings: Any) -> TokenEncryptor:
    """Build a ``TokenEncryptor`` from settings, failing loudly on misconfig.

    Reads the secret out of ``settings.token_enc_key`` (the SecretStr-wrapped
    ``COPILOT_TOKEN_ENC_KEY``) and feeds it through the env loader so the
    same validation rules apply whether the key was sourced from a real
    env var or a ``.env`` file. The token-encryption hard-fail is the
    distinguishing feature of issue 009: a missing key never silently
    falls back to plaintext storage.
    """
    raw = settings.token_enc_key.get_secret_value() if settings.token_enc_key else ""
    return load_encryptor_from_env({"COPILOT_TOKEN_ENC_KEY": raw})


def _maybe_build_title_summarizer(
    settings: Any, registry: ConversationRegistry
) -> HaikuTitleSummarizer | None:
    """Build the Haiku summarizer when an Anthropic key is available.

    Returns ``None`` when the key is missing — the chat path then skips the
    write-behind silently and the sidebar keeps its truncated-message
    placeholder. We deliberately don't fall back to the configured
    ``LLM_PROVIDER`` model: the issue calls for a Haiku-class model
    specifically, and using the (potentially Sonnet/GPT-4o) main model
    would silently inflate cost per turn.
    """
    api_key = settings.anthropic_api_key.get_secret_value()
    if not api_key:
        return None
    factory = build_default_haiku_factory(api_key)
    return HaikuTitleSummarizer(registry=registry, model_factory=factory)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    # open_checkpointer falls back to MemorySaver when CHECKPOINTER_DSN is
    # unset, so this same path serves dev (no DSN) and production (Postgres).
    async with open_checkpointer(settings) as checkpointer:
        app.state.graph = build_graph(settings, checkpointer=checkpointer)
        app.state.checkpointer = checkpointer
        # Session gateway and conversation registry for standalone auth +
        # sidebar metadata: Postgres-backed when DSN is set, in-memory
        # otherwise. Tests inject their own instances before entering the
        # lifespan, so respect pre-existing ones.
        existing_session = hasattr(app.state, "session_gateway")
        existing_conv = hasattr(app.state, "conversation_registry")
        existing_summarizer = hasattr(app.state, "title_summarizer")
        if existing_session and existing_conv and existing_summarizer:
            yield
            return
        if settings.checkpointer_dsn:
            # Token encryption-at-rest: required when persisting to
            # Postgres. ``load_encryptor_from_env`` raises a typed
            # error when the env var is missing or mis-shaped — the
            # process fails to start with a clear message rather than
            # silently writing plaintext tokens.
            encryptor = _load_token_encryptor(settings)
            async with open_session_store(
                settings.checkpointer_dsn, encryptor=encryptor
            ) as session_store:
                async with open_conversation_store(
                    settings.checkpointer_dsn
                ) as conv_store:
                    if not existing_session:
                        app.state.session_gateway = SessionGateway(
                            store=session_store
                        )
                    if not existing_conv:
                        app.state.conversation_registry = ConversationRegistry(
                            store=conv_store
                        )
                    if not existing_summarizer:
                        app.state.title_summarizer = _maybe_build_title_summarizer(
                            settings, app.state.conversation_registry
                        )
                    yield
        else:
            if not existing_session:
                app.state.session_gateway = SessionGateway(
                    store=InMemorySessionStore()
                )
            if not existing_conv:
                app.state.conversation_registry = ConversationRegistry(
                    store=InMemoryConversationStore()
                )
            if not existing_summarizer:
                app.state.title_summarizer = _maybe_build_title_summarizer(
                    settings, app.state.conversation_registry
                )
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


async def _resolve_fresh_standalone_bundle(
    session_id: str,
    gateway: SessionGateway,
    settings: Any,
) -> TokenBundleRow | None:
    """Return a non-expired token bundle for the standalone session.

    If the stored bundle is within the gateway's refresh-skew of expiring
    (or already expired), POSTs ``grant_type=refresh_token`` to the OpenEMR
    token endpoint and persists the rotated bundle before returning it.
    The user does not have to log in again.

    Returns ``None`` when:
    - the session has no stored bundle (typical in dev/test paths that
      skip the OAuth dance), or
    - the standalone OAuth client isn't configured (``SMART_STANDALONE_CLIENT_ID``
      is empty — no credentials to present at the token endpoint).

    Refresh failures (token endpoint refusal, e.g. revoked refresh token)
    propagate as ``RuntimeError`` so the caller can decide whether to fail
    the request or log and continue with an empty access token.
    """
    if not settings.smart_standalone_client_id:
        return await gateway.get_token_bundle(session_id)

    config = await discover_smart_endpoints(settings.openemr_fhir_base)
    token_endpoint = config.get("token_endpoint")
    if not token_endpoint:
        return await gateway.get_token_bundle(session_id)

    async def _refresh(rt: str) -> dict[str, Any]:
        return await refresh_access_token(
            token_endpoint=token_endpoint,
            refresh_token=rt,
            client_id=settings.smart_standalone_client_id,
            client_secret=settings.smart_standalone_client_secret.get_secret_value(),
        )

    return await gateway.get_fresh_token_bundle(session_id, refresh_fn=_refresh)


async def _seed_panel_registry(
    bundle: Any | None, user_id: str, settings: Any
) -> dict[str, dict[str, Any]]:
    """Build the ``resolved_patients`` seed for a standalone-path /chat call.

    Returns an empty dict (a no-op merge) for the EHR-launch path — that
    flow is single-patient by construction and ``resolve_patient`` is not
    in its critical path. Returns the user's CareTeam roster keyed by
    ``patient_id`` for the standalone path, so the first ``resolve_patient``
    in a click-to-brief turn is a cache hit.
    """
    if bundle is not None or not user_id:
        return {}
    gate = CareTeamGate(
        FhirClient(settings),
        admin_user_ids=frozenset(settings.admin_user_ids),
    )
    panel = await gate.list_panel(user_id)
    return {
        p.patient_id: {
            "patient_id": p.patient_id,
            "given_name": p.given_name,
            "family_name": p.family_name,
            "birth_date": p.birth_date,
        }
        for p in panel
    }


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
async def chat(
    req: ChatRequest,
    background_tasks: BackgroundTasks,
    copilot_session: str | None = Cookie(default=None),
) -> ChatResponse:
    graph = app.state.graph
    settings = app.state.settings

    # Resolve SMART context from stores when the body doesn't carry it.
    stores = get_default_stores()
    bundle = stores.get_token(req.conversation_id)
    patient_id = req.patient_id or (bundle.patient_id if bundle else "")
    user_id = req.user_id or (bundle.user_id if bundle else "")
    smart_access_token = req.smart_access_token or (bundle.access_token if bundle else "")

    # Standalone path: when the EHR-launch bundle is absent and the request
    # arrives with a session cookie, resolve the practitioner from the
    # session's ``fhir_user`` claim. This is the multi-patient flow — no
    # patient_id pin, the CareTeam gate at the tool layer is the
    # authorization boundary.
    if bundle is None and copilot_session and not user_id:
        gateway: SessionGateway = app.state.session_gateway
        session = await gateway.get_session(copilot_session)
        if session is not None:
            _, practitioner_id = parse_fhir_user(session.fhir_user)
            user_id = practitioner_id
            # Token refresh on access-token expiry.  When the body didn't
            # supply a token override, fetch the standalone bundle and
            # refresh it transparently if it's within skew of expiring —
            # so a chat call that arrives 7h59m into an 8h session still
            # gets a live access token without forcing the user back to
            # /auth/login.  Refresh failures (revoked refresh token, token
            # endpoint outage) are logged and the request continues with
            # an empty access token; the FHIR layer will then surface the
            # auth failure to the user, which the UI translates to a
            # re-login prompt.
            if not smart_access_token:
                try:
                    fresh_bundle = await _resolve_fresh_standalone_bundle(
                        copilot_session, gateway, settings
                    )
                except RuntimeError as exc:
                    _log.warning("standalone token refresh failed: %s", exc)
                    fresh_bundle = None
                if fresh_bundle is not None:
                    smart_access_token = fresh_bundle.access_token

    # EHR-launch path keeps its single-patient pin: the chart-sidebar embed
    # expects every /chat call to be scoped to the launched patient.
    # ``_assert_patient_context_matches`` is the boundary guard.
    if bundle is not None:
        if not patient_id:
            raise HTTPException(
                status_code=401,
                detail=(
                    "session has no bound patient context — re-launch the Co-Pilot "
                    "from the patient's chart"
                ),
            )
        _assert_patient_context_matches(req.conversation_id, patient_id)

    # Standalone-path registry seed (issue 005). Pre-populating
    # ``resolved_patients`` from the user's CareTeam roster makes the LLM's
    # first ``resolve_patient`` call (e.g., the click-to-brief synthetic
    # message) an O(1) cache hit — saving the FHIR ``CareTeam`` round-trip
    # the cold path would otherwise take. The reducer is right-wins so
    # later turns don't lose patients the user has resolved out of band
    # (admin-bypass cases that aren't on the panel).
    seeded_registry = await _seed_panel_registry(bundle, user_id, settings)

    config: dict[str, Any] = {"configurable": {"thread_id": req.conversation_id}}
    handler = get_callback_handler(settings)
    if handler is not None:
        config["callbacks"] = [handler]
    inputs: dict[str, Any] = {
        "messages": [HumanMessage(content=req.message)],
        "conversation_id": req.conversation_id,
        "patient_id": patient_id,
        "user_id": user_id,
        "smart_access_token": smart_access_token,
    }
    if seeded_registry:
        inputs["resolved_patients"] = seeded_registry
    result = await graph.ainvoke(inputs, config=config)
    messages = result.get("messages") or []
    if not messages:
        raise HTTPException(status_code=500, detail="graph returned no messages")
    reply_raw = messages[-1].content
    reply = reply_raw if isinstance(reply_raw, str) else str(reply_raw)

    # Sidebar write-behind. We touch the conversation row after the graph
    # has produced a result so a graph error doesn't leave a sidebar entry
    # for a turn that didn't actually happen. The registry is the only
    # place that owns sidebar-shape metadata; the LangGraph checkpointer
    # remains the source of truth for messages and CoPilotState.
    #
    # Auto-create on unknown id matches click-to-brief's flow: the front
    # end mints a conversation_id without an explicit POST /conversations
    # in some paths, so the first /chat call needs to register the row.
    # Skipped when there's no authenticated user — anonymous chat doesn't
    # land in any sidebar.
    registry: ConversationRegistry | None = getattr(
        app.state, "conversation_registry", None
    )
    if registry is not None and user_id:
        existing_row = await registry.get(req.conversation_id)
        if existing_row is None:
            await registry.create(
                conversation_id=req.conversation_id,
                user_id=user_id,
            )
        first_turn_title_written = await registry.ensure_first_turn_title(
            req.conversation_id, req.message
        )
        focus_pid = (
            result.get("focus_pid") or result.get("patient_id") or ""
        )
        await registry.touch(req.conversation_id, focus_pid=focus_pid)

        # Issue 008: schedule the Haiku title summarizer when this was the
        # first turn. Returns ``True`` exactly once per conversation —
        # ``ensure_first_turn_title`` no-ops on subsequent turns — so the
        # summarizer is invoked at most once. ``BackgroundTasks`` runs the
        # call after the response is sent so the user sees the chat reply
        # immediately and the better title lands a beat later.
        summarizer: HaikuTitleSummarizer | None = getattr(
            app.state, "title_summarizer", None
        )
        if first_turn_title_written and summarizer is not None:
            background_tasks.add_task(
                summarizer.summarize_and_set,
                conversation_id=req.conversation_id,
                first_user_message=req.message,
                first_assistant_message=reply,
            )

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
    #
    # Production now serves the UI from this same origin (StaticFiles mount
    # at "/"), so the redirect target defaults to "/" — relative, resolves to
    # the agent's own URL. ``COPILOT_UI_URL`` overrides for local dev where
    # the Vite dev server runs separately on :5173.
    #
    # Cookie attributes:
    # - same-origin prod (HTTPS) → ``SameSite=Lax; Secure``
    # - localhost dev (Vite proxy) → ``SameSite=Lax`` + insecure
    # We no longer need ``SameSite=None``; cross-site cookies are blocked by
    # third-party-cookie protections in modern browsers regardless.
    ui_url = settings.copilot_ui_url or "/"
    is_https = ui_url.startswith("https://") or ui_url == "/"
    response = RedirectResponse(url=ui_url, status_code=302)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        secure=is_https,
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

    # Bind the user's SMART access token to the FhirClient call. Without
    # this the client falls back to ``OPENEMR_FHIR_TOKEN`` (env-static) or
    # an empty token, and the FHIR ``CareTeam?status=active`` query 401s
    # — so the gate sees an empty bundle and the panel comes back empty
    # for every standalone-session user.
    bundle: TokenBundleRow | None = None
    try:
        bundle = await _resolve_fresh_standalone_bundle(
            copilot_session, gateway, settings
        )
    except RuntimeError as exc:
        # Refresh failure (revoked refresh token, etc.). Surface as 401 so
        # the UI prompts re-login rather than silently returning an empty
        # panel that looks like "you're on no care team."
        raise HTTPException(
            status_code=401,
            detail="session token refresh failed",
        ) from exc

    _, practitioner_id = parse_fhir_user(session.fhir_user)
    gate = CareTeamGate(
        FhirClient(settings),
        admin_user_ids=frozenset(settings.admin_user_ids),
    )
    token = bundle.access_token if bundle else ""
    set_active_smart_token(token or None)

    try:
        panel = await gate.list_panel(practitioner_id)
    finally:
        set_active_smart_token(None)
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


# ---------------------------------------------------------------------------
# Conversation sidebar endpoints
# ---------------------------------------------------------------------------


async def _resolve_user_id_from_cookie(copilot_session: str | None) -> str:
    """Map session cookie → Practitioner UUID. Raises 401 when unauthenticated."""
    if not copilot_session:
        raise HTTPException(status_code=401, detail="not authenticated")

    gateway: SessionGateway = app.state.session_gateway
    session = await gateway.get_session(copilot_session)
    if session is None:
        raise HTTPException(status_code=401, detail="session expired or invalid")

    _, practitioner_id = parse_fhir_user(session.fhir_user)
    return practitioner_id


@app.get("/conversations")
async def list_conversations(
    copilot_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Return the authenticated user's threads, ``updated_at DESC``.

    Powers the ConversationSidebar component. Archived rows are excluded by
    the registry; an admin / archive-recovery UI is deferred (the column
    exists but isn't surfaced in week 1).
    """
    user_id = await _resolve_user_id_from_cookie(copilot_session)
    registry: ConversationRegistry = app.state.conversation_registry
    rows = await registry.list_for_user(user_id)
    return {
        "conversations": [
            {
                "id": r.id,
                "title": r.title,
                "last_focus_pid": r.last_focus_pid,
                "updated_at": r.updated_at,
                "created_at": r.created_at,
            }
            for r in rows
        ],
    }


@app.post("/conversations")
async def create_conversation(
    copilot_session: str | None = Cookie(default=None),
) -> dict[str, str]:
    """Mint a fresh thread row. The returned id is usable as a LangGraph
    ``thread_id`` immediately — the next /chat call attaches turns to it.
    """
    user_id = await _resolve_user_id_from_cookie(copilot_session)
    registry: ConversationRegistry = app.state.conversation_registry

    conversation_id = secrets.token_urlsafe(16)
    await registry.create(conversation_id=conversation_id, user_id=user_id)
    return {"id": conversation_id}


@app.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str,
    copilot_session: str | None = Cookie(default=None),
) -> dict[str, Any]:
    """Return the prior turns for a conversation, loaded from the LangGraph
    checkpoint. Drives sidebar reopen — clicking a thread fetches its
    messages and rehydrates the chat surface.

    Returns turn pairs as ``[{role: 'user' | 'agent', content: str}, ...]``
    so the frontend doesn't have to know about LangChain message types.
    Authorization: the row must belong to the requesting user.
    """
    user_id = await _resolve_user_id_from_cookie(copilot_session)
    registry: ConversationRegistry = app.state.conversation_registry

    row = await registry.get(conversation_id)
    if row is None or row.user_id != user_id:
        # 404 vs 403 collapse — the privacy decision mirrors resolve_patient:
        # owner-mismatch is indistinguishable from non-existence.
        raise HTTPException(status_code=404, detail="conversation not found")

    messages: list[dict[str, str]] = []
    graph = getattr(app.state, "graph", None)
    if graph is not None and hasattr(graph, "aget_state"):
        config: dict[str, Any] = {"configurable": {"thread_id": conversation_id}}
        try:
            snapshot = await graph.aget_state(config)
        except Exception:
            # Missing checkpoint shouldn't fail the endpoint — return the
            # row metadata with an empty message list and let the UI handle
            # the rehydration gap.
            snapshot = None
        if snapshot is not None and snapshot.values:
            from langchain_core.messages import AIMessage, HumanMessage

            for m in snapshot.values.get("messages") or []:
                content = (
                    m.content if isinstance(m.content, str) else str(m.content or "")
                )
                if isinstance(m, HumanMessage):
                    messages.append({"role": "user", "content": content})
                elif isinstance(m, AIMessage):
                    # Skip empty AIMessages (tool-only turns); the user-
                    # visible transcript only carries the synthesized
                    # reply, not intermediate tool calls.
                    if content:
                        messages.append({"role": "agent", "content": content})

    return {
        "id": row.id,
        "title": row.title,
        "last_focus_pid": row.last_focus_pid,
        "messages": messages,
    }


# ---------------------------------------------------------------------------
# Document upload (issue 011)
# ---------------------------------------------------------------------------


_VALID_UPLOAD_DOC_TYPES: frozenset[str] = frozenset({"lab_pdf", "intake_form"})

# Mirror copilot-ui/src/api/upload.ts so the server-side cap matches the
# client-side cap. DocumentClient also re-checks at the storage boundary.
_MAX_UPLOAD_BYTES: int = 20 * 1024 * 1024


class UploadResponse(BaseModel):
    """Wire shape returned by ``POST /upload``.

    Mirrors copilot-ui/src/api/extraction.ts ``ExtractionResponse``: one of
    ``lab`` or ``intake`` is populated according to ``doc_type``; the other
    is ``None``. Both Pydantic extraction shapes are dumped via
    ``model_dump(mode="json")`` so the field set on the wire is determined
    by the schema, not by hand-maintained DTO mirroring.
    """

    document_id: str
    doc_type: str
    filename: str
    lab: dict[str, Any] | None = None
    intake: dict[str, Any] | None = None


def _resolve_upload_document_client(req_app: FastAPI) -> Any:
    """Return the DocumentClient bound to the app.

    Tests inject a stub via ``app.state.document_client``. Production
    builds one lazily from settings on first request.
    """
    existing = getattr(req_app.state, "document_client", None)
    if existing is not None:
        return existing
    settings = req_app.state.settings
    client = DocumentClient(settings)
    req_app.state.document_client = client
    return client


def _resolve_upload_vlm_model(req_app: FastAPI) -> Any:
    """Return the VLM model bound to the app, building lazily if needed."""
    existing = getattr(req_app.state, "vlm_model", None)
    if existing is not None:
        return existing
    from .llm import build_vision_model

    settings = req_app.state.settings
    model = build_vision_model(settings)
    req_app.state.vlm_model = model
    return model


def _resolve_upload_extraction_store(req_app: FastAPI) -> Any | None:
    """Return the upload cache store when configured.

    Tests inject ``app.state.extraction_store``. Production builds one
    lazily from ``CHECKPOINTER_DSN``; when no DSN is configured, upload
    still works but cache persistence is skipped.
    """
    existing = getattr(req_app.state, "extraction_store", None)
    if existing is not None:
        return existing
    settings = req_app.state.settings
    if not settings.checkpointer_dsn:
        return None
    store = DocumentExtractionStore(settings.checkpointer_dsn)
    req_app.state.extraction_store = store
    return store


def _sniff_mimetype(file_data: bytes, fallback: str | None) -> str:
    """Resolve a mimetype from the raw bytes, falling back to ``fallback``."""
    if file_data.startswith(b"%PDF-"):
        return "application/pdf"
    if file_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if file_data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return fallback or "application/octet-stream"


async def _persist_upload_extraction_cache(
    req_app: FastAPI,
    *,
    extraction: LabExtraction | IntakeExtraction,
    file_data: bytes,
    mimetype: str,
    doc_type: str,
    document_id: str,
    patient_id: str,
    filename: str,
) -> None:
    store = _resolve_upload_extraction_store(req_app)
    if store is None:
        return
    try:
        bboxes = match_extraction_to_bboxes(
            extraction,
            file_data,
            mimetype=mimetype,
        )
    except Exception as exc:  # pragma: no cover - cache best-effort
        _log.warning("upload cache bbox match failed: %s", exc)
        bboxes = []
    content_sha256 = hashlib.sha256(file_data).hexdigest()
    try:
        if isinstance(extraction, LabExtraction):
            await store.save_lab_extraction(
                extraction=extraction,
                bboxes=bboxes,
                document_id=document_id,
                patient_id=patient_id,
                filename=filename,
                content_sha256=content_sha256,
            )
        elif isinstance(extraction, IntakeExtraction):
            await store.save_intake_extraction(
                extraction=extraction,
                bboxes=bboxes,
                document_id=document_id,
                patient_id=patient_id,
                filename=filename,
                content_sha256=content_sha256,
            )
    except Exception as exc:  # pragma: no cover - cache best-effort
        _log.warning("upload cache persistence failed: %s", exc)


async def _inject_upload_system_message(
    req_app: FastAPI,
    conversation_id: str,
    doc_type: str,
    filename: str,
    document_id: str,
    patient_id: str,
) -> None:
    """Append a ``[system] Document uploaded …`` message to checkpointer state.

    The classifier reads this on the next ``/chat`` turn and routes to
    ``W-DOC``. Failures are logged but do not propagate — the UI's
    synthetic chat turn following the upload covers the routing path
    even if the checkpointer write fails.
    """
    checkpointer = getattr(req_app.state, "checkpointer", None)
    if checkpointer is None:
        return
    msg = build_document_upload_message(
        doc_type=doc_type,
        filename=filename,
        document_id=f"DocumentReference/{document_id}",
        patient_id=f"Patient/{patient_id}"
        if not patient_id.startswith("Patient/")
        else patient_id,
    )
    graph = getattr(req_app.state, "graph", None)
    if graph is None:
        return
    update_state = getattr(graph, "aupdate_state", None) or getattr(
        graph, "update_state", None
    )
    if update_state is None:
        return
    try:
        config = {"configurable": {"thread_id": conversation_id}}
        result = update_state(config, {"messages": [msg]})
        if hasattr(result, "__await__"):
            await result
    except Exception as exc:  # pragma: no cover - logged best-effort
        _log.warning("upload system-message injection failed: %s", exc)


_TRUTHY_FORM_VALUES: frozenset[str] = frozenset({"true", "1", "yes", "on"})


@app.post("/upload", response_model=UploadResponse)
async def upload(
    file: UploadFile = File(...),  # noqa: B008 - FastAPI dependency injection idiom
    patient_id: str = Form(default=""),
    doc_type: str = Form(default=""),
    confirm_doc_type: str = Form(default=""),
    conversation_id: str | None = Form(default=None),
    copilot_session: str | None = Cookie(default=None),
) -> UploadResponse:
    """Upload a clinical document, run VLM extraction, return the result.

    The endpoint is the agent-side glue between copilot-ui's
    FileUploadWidget and the document-extraction pipeline. It:

    1. Validates ``doc_type`` and ``patient_id`` shape.
    2. Reads the file bytes (rejects empty / oversized).
    3. Uploads to OpenEMR via :class:`DocumentClient`.
    4. Runs the VLM pipeline against the uploaded bytes (no re-download).
    5. Optionally injects a ``[system] Document uploaded …`` sentinel into
       conversation state so the classifier sees it on the next turn.
    6. Returns an ``UploadResponse`` mirroring the UI's TypeScript shape.
    """

    if doc_type not in _VALID_UPLOAD_DOC_TYPES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"invalid doc_type '{doc_type}'. "
                f"Expected one of: {sorted(_VALID_UPLOAD_DOC_TYPES)}"
            ),
        )
    if not patient_id:
        raise HTTPException(status_code=400, detail="patient_id is required")

    file_data = await file.read()
    if not file_data:
        raise HTTPException(status_code=400, detail="file is empty")
    if len(file_data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"file exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload cap"
            ),
        )

    # Defense-in-depth: when a SMART bundle is bound to this conversation,
    # ensure the upload's patient matches the launched patient.
    if conversation_id:
        _assert_patient_context_matches(conversation_id, patient_id)

    # Document-type guard (issue 024). Cheap deterministic check that
    # catches obvious lab-vs-intake mismatches before the wrong VLM
    # pipeline runs. The clinician can override by re-submitting with
    # ``confirm_doc_type=true``.
    filename = file.filename or "upload.bin"
    sniffed_mimetype = _sniff_mimetype(file_data, file.content_type)
    detection = detect_doc_type(file_data, filename, sniffed_mimetype)
    confirmed = confirm_doc_type.lower() in _TRUTHY_FORM_VALUES
    if (
        not confirmed
        and detection.detected_type is not None
        and detection.detected_type != doc_type
        and detection.confidence == "high"
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "doc_type_mismatch",
                "message": (
                    f"This file looks like a {detection.detected_type}, "
                    f"not a {doc_type}. Switch the document type or confirm to upload anyway."
                ),
                "requested_type": doc_type,
                "detected_type": detection.detected_type,
                "confidence": detection.confidence,
                "evidence": list(detection.evidence),
            },
        )

    # Resolve a SMART access token for OpenEMR. /chat threads it through
    # the graph state; /upload calls DocumentClient + VLM directly so we
    # bind it to the contextvar DocumentClient._resolve_token() reads
    # from. Without this the upload fails with "no_token".
    settings = app.state.settings
    stores = get_default_stores()
    bundle = stores.get_token(conversation_id) if conversation_id else None
    smart_access_token: str = bundle.access_token if bundle else ""
    if not smart_access_token and copilot_session:
        try:
            gateway: SessionGateway = app.state.session_gateway
            fresh = await _resolve_fresh_standalone_bundle(
                copilot_session, gateway, settings
            )
        except RuntimeError as exc:
            _log.warning("upload: standalone token refresh failed: %s", exc)
            fresh = None
        if fresh is not None:
            smart_access_token = fresh.access_token

    set_active_smart_token(smart_access_token or None)

    document_client = _resolve_upload_document_client(app)

    ok, document_id, err, _latency = await document_client.upload(
        patient_id,
        file_data,
        filename,
        doc_type,
    )
    if not ok or not document_id:
        if err == UPLOAD_LANDED_ID_LOST:
            raise HTTPException(
                status_code=502,
                detail=(
                    "upload landed but the document id couldn't be confirmed; "
                    "please re-attach"
                ),
            )
        raise HTTPException(
            status_code=502,
            detail=f"upload_failed: {err or 'unknown'}",
        )

    vlm_model = _resolve_upload_vlm_model(app)
    mimetype = sniffed_mimetype
    result = await _vlm_extract_document(
        file_data,
        mimetype,
        doc_type,  # type: ignore[arg-type]
        document_id=f"DocumentReference/{document_id}",
        model=vlm_model,
    )
    if not result.ok or result.extraction is None:
        raise HTTPException(
            status_code=502,
            detail=f"extraction_failed: {result.error or 'no extraction'}",
        )

    await _persist_upload_extraction_cache(
        app,
        extraction=result.extraction,
        file_data=file_data,
        mimetype=mimetype,
        doc_type=doc_type,
        document_id=document_id,
        patient_id=patient_id,
        filename=filename,
    )

    if conversation_id:
        await _inject_upload_system_message(
            app,
            conversation_id,
            doc_type,
            filename,
            document_id,
            patient_id,
        )

    extraction = result.extraction
    extraction_dump = extraction.model_dump(mode="json")
    lab_payload: dict[str, Any] | None = None
    intake_payload: dict[str, Any] | None = None
    if isinstance(extraction, LabExtraction):
        lab_payload = extraction_dump
    elif isinstance(extraction, IntakeExtraction):
        intake_payload = extraction_dump

    return UploadResponse(
        document_id=document_id,
        doc_type=doc_type,
        filename=filename,
        lab=lab_payload,
        intake=intake_payload,
    )


# Static UI mount — must be last so the API routes above take precedence.
# ``html=True`` makes StaticFiles fall back to ``index.html`` for paths that
# don't match a real file (SPA client-side routes). The directory is set in
# the Dockerfile via ``COPILOT_STATIC_DIR``; absent in test/dev runs, we
# skip the mount so unit tests don't need a built UI on disk.
_static_dir = os.environ.get("COPILOT_STATIC_DIR", "")
if _static_dir and Path(_static_dir).is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")
