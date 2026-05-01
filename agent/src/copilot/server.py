"""FastAPI entry point.

Exposes the chat surface (POST /chat), a health check, and stub SMART EHR
launch endpoints so the OAuth flow can be filled in without touching the
graph wiring.

Wire shapes (request and response) are defined in :mod:`copilot.api.schemas`
and mirror ``agentforge-docs/CHAT-API-CONTRACT.md``.
"""

from __future__ import annotations

import logging
import secrets
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
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
from .checkpointer import open_checkpointer
from .config import get_settings
from .graph import build_graph
from .observability import get_callback_handler
from .smart import (
    build_authorize_redirect_url,
    code_challenge_for,
    discover_smart_endpoints,
    exchange_code_for_token,
    generate_code_verifier,
    generate_state,
    get_default_stores,
    token_bundle_from_response,
    LaunchState,
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
    except Exception as exc:  # noqa: BLE001
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

    from urllib.parse import urlencode

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
