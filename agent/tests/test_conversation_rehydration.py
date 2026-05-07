"""GET /conversations/{id}/messages — structured-turn rehydration (issue 045).

Pins the contract: when the per-turn provenance store has rows for a
conversation, the messages endpoint surfaces them with their structured
``block`` and ``route`` metadata so the frontend can re-render the same
surface the clinician saw the first time. Legacy conversations (no turn
rows) fall back to a plain-text checkpoint scan.

Prior art: ``test_conversation_endpoints.py`` for the StubGraph + TestClient
pattern; this file adds endpoint-level coverage of the issue-045 contract.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from copilot.conversation_turns import (
    ConversationTurnRegistry,
    InMemoryTurnStore,
)
from copilot.conversations import (
    ConversationRegistry,
    InMemoryConversationStore,
)
from copilot.session import (
    InMemorySessionStore,
    SessionGateway,
    SessionRow,
)


class _StubGraphPlain:
    """Returns a plain ``ack`` response with chart-route metadata."""

    async def ainvoke(
        self, inputs: dict[str, Any], config: dict[str, Any]
    ) -> dict[str, Any]:
        from langchain_core.messages import AIMessage

        from copilot.api.schemas import PlainBlock

        block = PlainBlock(
            lead="Robert is stable.",
            citations=(
                {"card": "vitals", "label": "BP 120/76", "fhir_ref": "Observation/obs-1"},
            ),
            followups=("Show overnight events",),
        )
        return {
            "messages": [AIMessage(content="Robert is stable.")],
            "patient_id": "pat-robert",
            "focus_pid": "pat-robert",
            "workflow_id": "W-2",
            "classifier_confidence": 0.9,
            "block": block.model_dump(by_alias=True),
            "decision": "allow",
        }


@pytest.fixture
def conv_client(monkeypatch: pytest.MonkeyPatch):
    """TestClient with in-memory session, conversation, and turn registries."""
    monkeypatch.setenv("USE_FIXTURE_FHIR", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    from copilot import server as server_mod

    @asynccontextmanager
    async def _stub_checkpointer(_settings):
        yield None

    monkeypatch.setattr(
        server_mod, "build_graph", lambda *_a, **_kw: _StubGraphPlain()
    )
    monkeypatch.setattr(server_mod, "open_checkpointer", _stub_checkpointer)

    session_gateway = SessionGateway(store=InMemorySessionStore())
    conversation_registry = ConversationRegistry(store=InMemoryConversationStore())
    turn_registry = ConversationTurnRegistry(store=InMemoryTurnStore())

    with TestClient(server_mod.app) as client:
        server_mod.app.state.session_gateway = session_gateway
        server_mod.app.state.conversation_registry = conversation_registry
        server_mod.app.state.conversation_turn_registry = turn_registry
        server_mod.app.state.title_summarizer = None
        server_mod.app.state.graph = _StubGraphPlain()
        yield client


async def _seed_session(
    fhir_user: str = "Practitioner/practitioner-dr-smith",
    *,
    display_name: str = "Dr. Smith",
) -> str:
    from copilot import server as server_mod

    gateway = server_mod.app.state.session_gateway
    sid = f"sess-{fhir_user.replace('/', '-')}"
    now = time.time()
    await gateway.create_session(
        SessionRow(
            session_id=sid,
            oe_user_id=42,
            display_name=display_name,
            fhir_user=fhir_user,
            created_at=now,
            expires_at=now + 3600,
        )
    )
    return sid


# ---------- /chat writes a turn row that /messages surfaces ----------


async def test_chat_writes_turn_row_persists_block_and_route(
    conv_client: TestClient,
) -> None:
    """End-to-end: a /chat turn writes a structured turn row, and the
    messages endpoint returns it with the same block + route the chat
    handler emitted. AC: persisted assistant turns can retain their
    structured block, route metadata, and citation metadata."""
    cookie = await _seed_session()
    new_id = conv_client.post(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["id"]

    chat_resp = conv_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={"conversation_id": new_id, "message": "How is Robert?"},
    )
    assert chat_resp.status_code == 200, chat_resp.text

    msgs_resp = conv_client.get(
        f"/conversations/{new_id}/messages",
        cookies={"copilot_session": cookie},
    )
    assert msgs_resp.status_code == 200
    body = msgs_resp.json()
    assert body["id"] == new_id

    # User + agent rows in order.
    assert len(body["messages"]) == 2
    user_msg, agent_msg = body["messages"]
    assert user_msg == {"role": "user", "content": "How is Robert?"}

    # Agent row carries the structured block.
    assert agent_msg["role"] == "agent"
    assert agent_msg["content"] == "Robert is stable."
    assert agent_msg["block"]["kind"] == "plain"
    assert agent_msg["block"]["lead"] == "Robert is stable."

    # Citations survive — that's the source-chip rehydration AC.
    assert len(agent_msg["block"]["citations"]) == 1
    assert agent_msg["block"]["citations"][0] == {
        "card": "vitals",
        "label": "BP 120/76",
        "fhir_ref": "Observation/obs-1",
    }
    # Followups survive too.
    assert agent_msg["block"]["followups"] == ["Show overnight events"]


async def test_chat_messages_endpoint_returns_route_metadata(
    conv_client: TestClient,
) -> None:
    """AC: conversation rehydration restores route labels for prior
    assistant answers. The route badge is the visible transparency surface,
    so the kind + label must round-trip exactly as derived by the chat
    handler (``derive_route_metadata``)."""
    cookie = await _seed_session()
    new_id = conv_client.post(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["id"]

    conv_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={"conversation_id": new_id, "message": "How is Robert?"},
    )

    body = conv_client.get(
        f"/conversations/{new_id}/messages",
        cookies={"copilot_session": cookie},
    ).json()

    agent_msg = body["messages"][1]
    # W-2 + decision=allow → chart route.
    assert agent_msg["route"] == {
        "kind": "chart",
        "label": "Reading the patient record",
    }


async def test_chat_messages_endpoint_returns_diagnostics(
    conv_client: TestClient,
) -> None:
    """The Technical-details affordance reads decision + supervisor_action;
    rehydration must carry both so the affordance is identical on reload."""
    cookie = await _seed_session()
    new_id = conv_client.post(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["id"]

    conv_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={"conversation_id": new_id, "message": "How is Robert?"},
    )

    body = conv_client.get(
        f"/conversations/{new_id}/messages",
        cookies={"copilot_session": cookie},
    ).json()

    agent_msg = body["messages"][1]
    assert agent_msg["diagnostics"] == {
        "decision": "allow",
        "supervisor_action": "",
    }
    assert agent_msg["workflow_id"] == "W-2"
    assert agent_msg["classifier_confidence"] == 0.9


async def test_multiple_turns_all_rehydrated_in_order(
    conv_client: TestClient,
) -> None:
    """A multi-turn conversation rehydrates every prior turn with its own
    block + route. Turn order must be preserved."""
    cookie = await _seed_session()
    new_id = conv_client.post(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["id"]

    for q in ("How is Robert?", "And his vitals?", "Anything overnight?"):
        conv_client.post(
            "/chat",
            cookies={"copilot_session": cookie},
            json={"conversation_id": new_id, "message": q},
        )

    body = conv_client.get(
        f"/conversations/{new_id}/messages",
        cookies={"copilot_session": cookie},
    ).json()

    # Three turn pairs → six messages in order, alternating user/agent.
    assert [m["role"] for m in body["messages"]] == [
        "user",
        "agent",
        "user",
        "agent",
        "user",
        "agent",
    ]
    user_questions = [m["content"] for m in body["messages"] if m["role"] == "user"]
    assert user_questions == [
        "How is Robert?",
        "And his vitals?",
        "Anything overnight?",
    ]
    # Every agent row carries a structured block — no flattening.
    for m in body["messages"]:
        if m["role"] == "agent":
            assert "block" in m
            assert m["block"]["kind"] == "plain"
            assert "route" in m


# ---------- legacy fallback: no turn rows → checkpoint scan ----------


async def test_legacy_conversation_with_no_turn_rows_falls_back_to_plain(
    conv_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the turn store has no rows for a conversation but the
    LangGraph checkpoint does, the endpoint returns plain-text turn
    pairs. AC: legacy conversations without structured metadata still
    render safely as plain text."""
    from copilot import server as server_mod

    cookie = await _seed_session()
    new_id = conv_client.post(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["id"]

    # Stub a graph that exposes ``aget_state`` so the legacy fallback
    # path runs. We DO NOT call /chat — so no turn rows get written.
    class _LegacyGraph:
        class _Snapshot:
            def __init__(self, values: dict[str, Any]) -> None:
                self.values = values

        async def aget_state(self, _config):
            from langchain_core.messages import AIMessage, HumanMessage

            return _LegacyGraph._Snapshot(
                values={
                    "messages": [
                        HumanMessage(content="legacy question"),
                        AIMessage(content="legacy answer"),
                    ]
                }
            )

    server_mod.app.state.graph = _LegacyGraph()

    body = conv_client.get(
        f"/conversations/{new_id}/messages",
        cookies={"copilot_session": cookie},
    ).json()

    assert len(body["messages"]) == 2
    user_msg, agent_msg = body["messages"]
    assert user_msg == {"role": "user", "content": "legacy question"}
    # Plain-text turn — no structured block, no route.
    assert agent_msg["role"] == "agent"
    assert agent_msg["content"] == "legacy answer"
    assert "block" not in agent_msg
    assert "route" not in agent_msg


async def test_messages_endpoint_unauthorized_for_other_user_with_turn_rows(
    conv_client: TestClient,
) -> None:
    """Privacy guard survives the issue-045 wire-in: a user can't read
    another user's structured turn rows by guessing the conversation id."""
    smith_cookie = await _seed_session(
        "Practitioner/practitioner-dr-smith", display_name="Dr. Smith"
    )
    other_cookie = await _seed_session(
        "Practitioner/practitioner-other", display_name="Dr. Other"
    )

    new_id = conv_client.post(
        "/conversations", cookies={"copilot_session": smith_cookie}
    ).json()["id"]

    # Smith fires a chat turn so a turn row exists.
    conv_client.post(
        "/chat",
        cookies={"copilot_session": smith_cookie},
        json={"conversation_id": new_id, "message": "How is Robert?"},
    )

    resp = conv_client.get(
        f"/conversations/{new_id}/messages",
        cookies={"copilot_session": other_cookie},
    )
    assert resp.status_code == 404


async def test_messages_endpoint_skips_turn_write_for_anonymous_chat(
    conv_client: TestClient,
) -> None:
    """Anonymous chat (no authenticated user) should not write turn rows
    — there's no sidebar entry to rehydrate, and writing a row keyed by
    conversation_id with no owner would be tracked indefinitely."""
    from copilot import server as server_mod

    # Use a fresh conversation_id with no session cookie.
    chat_resp = conv_client.post(
        "/chat",
        json={"conversation_id": "anon-thread-1", "message": "hi"},
    )
    # /chat itself succeeds for anonymous flows under the test stub.
    assert chat_resp.status_code == 200

    turn_registry = server_mod.app.state.conversation_turn_registry
    turns = await turn_registry.list_turns("anon-thread-1")
    assert turns == []
