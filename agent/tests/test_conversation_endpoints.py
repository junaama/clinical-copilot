"""Tests for the conversation sidebar HTTP surface.

Covers:

- ``POST /conversations`` mints a row scoped to the authenticated user.
- ``GET /conversations`` lists the user's rows in ``updated_at DESC``,
  excluding archived rows, ignoring rows owned by another user.
- ``POST /chat`` write-behind: title is set on first turn, ``updated_at``
  advances after every turn, ``last_focus_pid`` follows the graph's
  ``focus_pid`` output.
- ``GET /conversations/{id}/messages`` returns the prior turn-pair shapes
  loaded from the LangGraph checkpoint.

Prior art: ``test_chat_contract.py`` for the StubGraph + TestClient pattern;
``test_auth_endpoints.py`` for session injection.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from copilot.conversations import (
    ConversationRegistry,
    InMemoryConversationStore,
)
from copilot.session import (
    InMemorySessionStore,
    SessionGateway,
    SessionRow,
)


class _StubGraph:
    """Deterministic graph that returns whatever focus_pid the test sets.

    Mirrors test_chat_contract's _StubGraph but lets each test pick the
    focus_pid the agent_node would have written.
    """

    def __init__(self, focus_pid: str = "fixture-1") -> None:
        self.focus_pid = focus_pid

    async def ainvoke(
        self, inputs: dict[str, Any], config: dict[str, Any]
    ) -> dict[str, Any]:
        from langchain_core.messages import AIMessage

        from copilot.api.schemas import PlainBlock

        block = PlainBlock(lead="ack")
        return {
            "messages": [AIMessage(content="ack")],
            "patient_id": inputs.get("patient_id") or self.focus_pid,
            "focus_pid": self.focus_pid,
            "workflow_id": "W-2",
            "classifier_confidence": 0.9,
            "block": block.model_dump(by_alias=True),
        }


@pytest.fixture
def conv_client(monkeypatch: pytest.MonkeyPatch):
    """TestClient with in-memory session + conversation registries and a stub graph."""
    monkeypatch.setenv("USE_FIXTURE_FHIR", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    from copilot import server as server_mod

    @asynccontextmanager
    async def _stub_checkpointer(_settings):
        yield None

    monkeypatch.setattr(server_mod, "build_graph", lambda *_a, **_kw: _StubGraph())
    monkeypatch.setattr(server_mod, "open_checkpointer", _stub_checkpointer)

    session_gateway = SessionGateway(store=InMemorySessionStore())
    conversation_registry = ConversationRegistry(store=InMemoryConversationStore())

    with TestClient(server_mod.app) as client:
        server_mod.app.state.session_gateway = session_gateway
        server_mod.app.state.conversation_registry = conversation_registry
        # Issue 008: default to None so the chat write-behind skips the
        # Haiku call. Tests that exercise the summarizer wire override this
        # to a stub that records the invocation synchronously.
        server_mod.app.state.title_summarizer = None
        server_mod.app.state.graph = _StubGraph()
        yield client


async def _seed_session(
    fhir_user: str = "Practitioner/practitioner-dr-smith",
    *,
    display_name: str = "Dr. Smith",
) -> str:
    """Mint a session row directly so endpoint tests don't need OAuth."""
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


# ---------- POST /conversations ----------


async def test_post_conversations_returns_id(conv_client: TestClient) -> None:
    """POST /conversations mints a fresh row and returns its id.

    The id must be usable as a LangGraph thread_id immediately — the client
    will navigate to /c/<id> and the next /chat call uses it as
    conversation_id.
    """
    cookie = await _seed_session()
    resp = conv_client.post(
        "/conversations", cookies={"copilot_session": cookie}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body["id"], str) and body["id"]


async def test_post_conversations_persists_row_for_user(
    conv_client: TestClient,
) -> None:
    cookie = await _seed_session()
    resp = conv_client.post(
        "/conversations", cookies={"copilot_session": cookie}
    )
    new_id = resp.json()["id"]

    list_resp = conv_client.get(
        "/conversations", cookies={"copilot_session": cookie}
    )
    rows = list_resp.json()["conversations"]
    assert any(r["id"] == new_id for r in rows)


def test_post_conversations_requires_auth(conv_client: TestClient) -> None:
    resp = conv_client.post("/conversations")
    assert resp.status_code == 401


# ---------- GET /conversations ----------


async def test_get_conversations_scoped_to_user(conv_client: TestClient) -> None:
    """Another user's rows must not appear in this user's sidebar."""
    smith_cookie = await _seed_session(
        "Practitioner/practitioner-dr-smith", display_name="Dr. Smith"
    )
    other_cookie = await _seed_session(
        "Practitioner/practitioner-other", display_name="Dr. Other"
    )

    smith_id = conv_client.post(
        "/conversations", cookies={"copilot_session": smith_cookie}
    ).json()["id"]
    other_id = conv_client.post(
        "/conversations", cookies={"copilot_session": other_cookie}
    ).json()["id"]

    resp = conv_client.get(
        "/conversations", cookies={"copilot_session": smith_cookie}
    )
    rows = resp.json()["conversations"]
    ids = {r["id"] for r in rows}
    assert smith_id in ids
    assert other_id not in ids


async def test_get_conversations_orders_by_updated_at_desc(
    conv_client: TestClient,
) -> None:
    """Sidebar shows the most-recently-touched conversation first."""
    import asyncio

    cookie = await _seed_session()
    first = conv_client.post(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["id"]
    await asyncio.sleep(0.01)
    second = conv_client.post(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["id"]

    resp = conv_client.get(
        "/conversations", cookies={"copilot_session": cookie}
    )
    rows = resp.json()["conversations"]
    # Most recent first
    assert rows[0]["id"] == second
    assert rows[1]["id"] == first


def test_get_conversations_requires_auth(conv_client: TestClient) -> None:
    resp = conv_client.get("/conversations")
    assert resp.status_code == 401


# ---------- /chat write-behind ----------


async def test_chat_first_turn_sets_title_to_truncated_message(
    conv_client: TestClient,
) -> None:
    """First-turn message becomes the row's title, truncated to 60 chars.

    Issue 008 will swap this for a Haiku summary on a separate write-behind
    pass; until that ships this is the title users see.
    """
    cookie = await _seed_session()
    new_id = conv_client.post(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["id"]

    long_msg = "Tell me about Eduardo " + "x" * 200
    chat_resp = conv_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={"conversation_id": new_id, "message": long_msg},
    )
    assert chat_resp.status_code == 200, chat_resp.text

    list_resp = conv_client.get(
        "/conversations", cookies={"copilot_session": cookie}
    )
    row = next(r for r in list_resp.json()["conversations"] if r["id"] == new_id)
    assert row["title"].startswith("Tell me about Eduardo")
    assert len(row["title"]) <= 60


async def test_chat_subsequent_turns_do_not_overwrite_title(
    conv_client: TestClient,
) -> None:
    cookie = await _seed_session()
    new_id = conv_client.post(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["id"]

    conv_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={"conversation_id": new_id, "message": "First turn"},
    )
    conv_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={"conversation_id": new_id, "message": "Second turn — different topic"},
    )

    rows = conv_client.get(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["conversations"]
    row = next(r for r in rows if r["id"] == new_id)
    assert row["title"] == "First turn"


async def test_chat_persists_focus_pid_on_each_turn(
    conv_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The graph's focus_pid output flows into copilot_conversation.last_focus_pid."""
    from copilot import server as server_mod

    cookie = await _seed_session()
    new_id = conv_client.post(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["id"]

    server_mod.app.state.graph = _StubGraph(focus_pid="fixture-3")
    conv_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={"conversation_id": new_id, "message": "tell me about Robert"},
    )

    rows = conv_client.get(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["conversations"]
    row = next(r for r in rows if r["id"] == new_id)
    assert row["last_focus_pid"] == "fixture-3"


async def test_chat_creates_row_for_unknown_conversation_id(
    conv_client: TestClient,
) -> None:
    """A /chat call with a fresh conversation_id (no prior POST /conversations)
    auto-registers the row so it appears in the sidebar. Click-to-brief
    flows through this path — the front-end mints an id without an explicit
    create call when the panel-click flow lands.
    """
    cookie = await _seed_session()
    fresh_id = "fresh-thread-" + str(int(time.time() * 1000))

    chat_resp = conv_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={"conversation_id": fresh_id, "message": "Brief on Eduardo"},
    )
    assert chat_resp.status_code == 200

    rows = conv_client.get(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["conversations"]
    assert any(r["id"] == fresh_id for r in rows)


async def test_chat_without_session_skips_registry_write(
    conv_client: TestClient,
) -> None:
    """No session cookie → no user_id → no sidebar row written.

    Falls back to the prior behavior so EHR-launch and direct-API callers
    don't accidentally pollute someone else's sidebar.
    """
    fresh_id = "no-session-" + str(int(time.time() * 1000))
    chat_resp = conv_client.post(
        "/chat",
        json={"conversation_id": fresh_id, "message": "hi", "user_id": ""},
    )
    # The chat itself works without a session (legacy compat).
    assert chat_resp.status_code == 200

    # No row should have been created — registry is empty. We can't list
    # without a session, but we can check the registry directly.
    from copilot import server as server_mod

    registry = server_mod.app.state.conversation_registry
    assert await registry.get(fresh_id) is None


# ---------- GET /conversations/{id}/messages ----------


async def test_get_messages_returns_404_for_unknown(
    conv_client: TestClient,
) -> None:
    cookie = await _seed_session()
    resp = conv_client.get(
        "/conversations/never-created/messages",
        cookies={"copilot_session": cookie},
    )
    assert resp.status_code == 404


async def test_get_messages_returns_404_for_other_users_thread(
    conv_client: TestClient,
) -> None:
    """Privacy: owner-mismatch is indistinguishable from non-existence.

    Mirrors resolve_patient's collapse of "off-team" with "doesn't exist".
    """
    smith_cookie = await _seed_session(
        "Practitioner/practitioner-dr-smith", display_name="Dr. Smith"
    )
    other_cookie = await _seed_session(
        "Practitioner/practitioner-other", display_name="Dr. Other"
    )

    new_id = conv_client.post(
        "/conversations", cookies={"copilot_session": smith_cookie}
    ).json()["id"]

    # The other user attempts to read smith's thread.
    resp = conv_client.get(
        f"/conversations/{new_id}/messages",
        cookies={"copilot_session": other_cookie},
    )
    assert resp.status_code == 404


async def test_get_messages_returns_metadata_for_owner(
    conv_client: TestClient,
) -> None:
    """The endpoint returns the thread row's metadata even when the
    checkpointer is unavailable (e.g. test mode); ``messages`` is empty in
    that case but the response is still well-formed.
    """
    cookie = await _seed_session()
    new_id = conv_client.post(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["id"]

    resp = conv_client.get(
        f"/conversations/{new_id}/messages",
        cookies={"copilot_session": cookie},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == new_id
    assert "messages" in body
    assert isinstance(body["messages"], list)


def test_get_messages_requires_auth(conv_client: TestClient) -> None:
    resp = conv_client.get("/conversations/anything/messages")
    assert resp.status_code == 401


# ---------- Haiku title summarizer wire-in (issue 008) ----------


class _StubSummarizer:
    """Records every call and writes a deterministic title via the registry.

    BackgroundTasks invokes this synchronously after the response is sent,
    so the test's next ``GET /conversations`` sees the post-summarize title.
    """

    def __init__(self, registry: ConversationRegistry, title: str = "Haiku title") -> None:
        self._registry = registry
        self._title = title
        self.calls: list[dict[str, str]] = []

    async def summarize_and_set(
        self,
        *,
        conversation_id: str,
        first_user_message: str,
        first_assistant_message: str,
    ) -> None:
        self.calls.append(
            {
                "conversation_id": conversation_id,
                "first_user_message": first_user_message,
                "first_assistant_message": first_assistant_message,
            }
        )
        await self._registry.set_title(conversation_id, self._title)


async def test_chat_first_turn_invokes_summarizer_once(
    conv_client: TestClient,
) -> None:
    """First turn fires the summarizer exactly once; subsequent turns don't."""
    from copilot import server as server_mod

    cookie = await _seed_session()
    new_id = conv_client.post(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["id"]

    stub = _StubSummarizer(server_mod.app.state.conversation_registry)
    server_mod.app.state.title_summarizer = stub

    # Turn 1 — fires the summarizer.
    conv_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={"conversation_id": new_id, "message": "Tell me about Eduardo"},
    )
    # Turn 2 — must not fire again.
    conv_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={"conversation_id": new_id, "message": "And his vitals?"},
    )

    assert len(stub.calls) == 1
    call = stub.calls[0]
    assert call["conversation_id"] == new_id
    assert call["first_user_message"] == "Tell me about Eduardo"
    # Reply is whatever the stub graph emitted ("ack").
    assert call["first_assistant_message"] == "ack"


async def test_chat_summarizer_replaces_truncated_title_in_sidebar(
    conv_client: TestClient,
) -> None:
    """End-to-end: after the first /chat call, the sidebar shows the
    summarizer's title, not the truncated first message."""
    from copilot import server as server_mod

    cookie = await _seed_session()
    new_id = conv_client.post(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["id"]

    server_mod.app.state.title_summarizer = _StubSummarizer(
        server_mod.app.state.conversation_registry,
        title="Eduardo Perez 24h Brief",
    )

    conv_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={
            "conversation_id": new_id,
            "message": "Tell me about Eduardo",
        },
    )

    rows = conv_client.get(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["conversations"]
    row = next(r for r in rows if r["id"] == new_id)
    assert row["title"] == "Eduardo Perez 24h Brief"


async def test_chat_skips_summarizer_when_unconfigured(
    conv_client: TestClient,
) -> None:
    """No summarizer configured → chat still works, title stays as the
    truncated first message."""
    from copilot import server as server_mod

    cookie = await _seed_session()
    new_id = conv_client.post(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["id"]

    server_mod.app.state.title_summarizer = None

    chat_resp = conv_client.post(
        "/chat",
        cookies={"copilot_session": cookie},
        json={"conversation_id": new_id, "message": "Tell me about Eduardo"},
    )
    assert chat_resp.status_code == 200

    rows = conv_client.get(
        "/conversations", cookies={"copilot_session": cookie}
    ).json()["conversations"]
    row = next(r for r in rows if r["id"] == new_id)
    assert row["title"] == "Tell me about Eduardo"
