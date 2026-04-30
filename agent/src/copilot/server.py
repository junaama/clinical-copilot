"""FastAPI entry point.

Exposes the chat surface (POST /chat), a health check, and stub SMART EHR
launch endpoints so the OAuth flow can be filled in without touching the
graph wiring.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

from .config import get_settings
from .graph import build_graph
from .observability import get_callback_handler


class ChatRequest(BaseModel):
    conversation_id: str = Field(..., min_length=1)
    patient_id: str = Field(..., min_length=1)
    user_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    smart_access_token: str = Field(default="", description="Bearer token from SMART launch")


class ChatResponse(BaseModel):
    conversation_id: str
    reply: str
    state: dict[str, Any]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.settings = get_settings()
    app.state.graph = build_graph(app.state.settings)
    yield


app = FastAPI(title="OpenEMR Clinical Co-Pilot", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    graph = app.state.graph
    settings = app.state.settings
    config: dict[str, Any] = {"configurable": {"thread_id": req.conversation_id}}
    handler = get_callback_handler(settings)
    if handler is not None:
        config["callbacks"] = [handler]
    inputs = {
        "messages": [HumanMessage(content=req.message)],
        "conversation_id": req.conversation_id,
        "patient_id": req.patient_id,
        "user_id": req.user_id,
        "smart_access_token": req.smart_access_token,
    }
    result = await graph.ainvoke(inputs, config=config)
    messages = result.get("messages") or []
    if not messages:
        raise HTTPException(status_code=500, detail="graph returned no messages")
    reply = messages[-1].content
    return ChatResponse(
        conversation_id=req.conversation_id,
        reply=reply if isinstance(reply, str) else str(reply),
        state={
            "patient_id": result.get("patient_id"),
            "workflow_id": result.get("workflow_id"),
            "message_count": len(messages),
        },
    )


@app.get("/smart/launch")
def smart_launch(iss: str = "", launch: str = "") -> dict[str, str]:
    """SMART EHR launch entry point. To be implemented (PKCE + state + redirect to /authorize)."""
    if not iss or not launch:
        raise HTTPException(status_code=400, detail="missing iss/launch")
    return {"todo": "redirect to authorization endpoint", "iss": iss, "launch": launch}


@app.get("/smart/callback")
def smart_callback(code: str = "", state: str = "") -> dict[str, str]:
    """OAuth2 redirect target. To be implemented (token exchange + session bind)."""
    if not code:
        raise HTTPException(status_code=400, detail="missing code")
    return {"todo": "exchange code for access_token, bind to conversation", "state": state}
