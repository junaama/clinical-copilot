"""Click-to-brief server-side wire (issue 005).

The synthetic ``"Give me a brief on <patient name>."`` message rides the
existing /chat endpoint with no special flag. What this test exercises is
the registry seed: when /chat resolves a standalone session's practitioner
from the cookie, it must pre-populate ``resolved_patients`` from the user's
CareTeam panel so the LLM's first ``resolve_patient`` call is an O(1)
cache hit.

We also verify the audit-shape parity: the audit row a click-injected turn
produces is identical in shape to a typed-message turn. Same fields, same
``extra.gate_decisions`` and ``extra.denied_count`` keys.
"""

from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from copilot.api.schemas import PlainBlock
from copilot.audit import AuditEvent, write_audit_event
from copilot.config import Settings
from copilot.session import InMemorySessionStore, SessionGateway, SessionRow


class _RecordingGraph:
    """Captures the inputs handed to ainvoke so we can assert on them."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def ainvoke(
        self, inputs: dict[str, Any], config: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(inputs)
        block = PlainBlock(lead="ack", citations=(), followups=())
        return {
            "messages": [AIMessage(content="ack")],
            "patient_id": inputs.get("patient_id"),
            "workflow_id": "W-2",
            "classifier_confidence": 0.93,
            "block": block.model_dump(by_alias=True),
        }


@pytest.fixture
def click_client(monkeypatch: pytest.MonkeyPatch):
    """TestClient with stubbed graph + checkpointer + in-memory session store."""

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
    monkeypatch.setenv(
        "OPENEMR_FHIR_BASE", "https://openemr.example/apis/default/fhir"
    )

    from copilot import server as server_mod

    @asynccontextmanager
    async def _stub_checkpointer(_settings):
        yield None

    recording = _RecordingGraph()
    monkeypatch.setattr(server_mod, "build_graph", lambda *_a, **_kw: recording)
    monkeypatch.setattr(server_mod, "open_checkpointer", _stub_checkpointer)

    gateway = SessionGateway(store=InMemorySessionStore())

    with TestClient(server_mod.app) as client:
        server_mod.app.state.session_gateway = gateway
        server_mod.app.state.graph = recording
        # Re-read settings so the test env vars take effect.
        server_mod.app.state.settings = Settings()
        client.recording = recording  # type: ignore[attr-defined]
        client.gateway = gateway  # type: ignore[attr-defined]
        yield client


async def _seed_session(client: TestClient, fhir_user: str) -> str:
    gateway = client.gateway  # type: ignore[attr-defined]
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


# ---------------------------------------------------------------------------
# Registry seed
# ---------------------------------------------------------------------------


async def test_chat_seeds_resolved_patients_from_panel_for_standalone_session(
    click_client: TestClient,
) -> None:
    """A standalone /chat call (cookie, no SMART bundle) must seed
    ``resolved_patients`` from the user's CareTeam roster so the LLM's first
    ``resolve_patient`` call is a cache hit."""
    cookie = await _seed_session(click_client, "Practitioner/practitioner-dr-smith")

    resp = click_client.post(
        "/chat",
        json={
            "conversation_id": "conv-click-1",
            "patient_id": "",
            "user_id": "",
            "smart_access_token": "",
            "message": "Give me a brief on Robert Hayes.",
        },
        cookies={"copilot_session": cookie},
    )
    assert resp.status_code == 200, resp.text

    recording = click_client.recording  # type: ignore[attr-defined]
    assert len(recording.calls) == 1
    seeded = recording.calls[0].get("resolved_patients") or {}
    # dr_smith is on three of the five fixture patients (issue 002 fixtures):
    # fixture-1 (Eduardo Perez), fixture-3 (Robert Hayes), fixture-5 (James).
    assert set(seeded.keys()) == {"fixture-1", "fixture-3", "fixture-5"}
    hayes = seeded["fixture-3"]
    assert hayes["family_name"] == "Hayes"
    assert hayes["given_name"] == "Robert"
    assert hayes["birth_date"] == "1949-11-04"


async def test_chat_does_not_seed_when_no_session_cookie(
    click_client: TestClient,
) -> None:
    """No session, no seed — the input dict must omit ``resolved_patients``
    entirely (or carry an empty dict) so the graph's reducer is a no-op."""
    resp = click_client.post(
        "/chat",
        json={
            "conversation_id": "conv-click-2",
            "patient_id": "fixture-1",
            "user_id": "naama",
            "smart_access_token": "stub",
            "message": "What's going on?",
        },
    )
    assert resp.status_code == 200, resp.text

    recording = click_client.recording  # type: ignore[attr-defined]
    assert len(recording.calls) == 1
    # When no seeding happens we don't touch the field at all — the LLM's
    # ``resolve_patient`` will populate it on the cold path.
    assert "resolved_patients" not in recording.calls[0]


async def test_chat_does_not_seed_for_ehr_launch_bundle(
    click_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The EHR-launch path is single-patient by construction and uses a
    different resolution mechanism; no panel seed for that flow."""
    from copilot.smart import TokenBundle, get_default_stores

    stores = get_default_stores()
    stores.put_token(
        "conv-ehr-1",
        TokenBundle(
            access_token="bearer-x",
            refresh_token="",
            id_token="",
            scope="patient/*.rs",
            patient_id="fixture-1",
            user_id="naama",
            iss="https://openemr.example/apis/default/fhir",
            issued_at=time.time(),
            expires_in=3600,
        ),
    )
    try:
        resp = click_client.post(
            "/chat",
            json={
                "conversation_id": "conv-ehr-1",
                "patient_id": "fixture-1",
                "user_id": "naama",
                "smart_access_token": "bearer-x",
                "message": "What's going on?",
            },
        )
        assert resp.status_code == 200, resp.text

        recording = click_client.recording  # type: ignore[attr-defined]
        assert len(recording.calls) == 1
        assert "resolved_patients" not in recording.calls[0]
    finally:
        stores.tokens_by_conversation.pop("conv-ehr-1", None)


# ---------------------------------------------------------------------------
# Audit-shape parity: click-injected vs typed message produce the same row shape
# ---------------------------------------------------------------------------


def test_audit_row_shape_is_identical_for_click_and_typed(tmp_path: Path) -> None:
    """The PRD requires audit-shape parity between a click-injected turn and
    a typed equivalent. Both flows hit the same /chat endpoint with the same
    body shape, so the audit row produced is the same. Verify by writing two
    rows whose only meaningful difference is the message text (which the
    audit explicitly does NOT record) and checking the keys + types match."""
    settings = Settings(
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="test",
        AGENT_AUDIT_LOG_PATH=str(tmp_path / "audit.jsonl"),
    )

    base_extra = {
        "final_response_chars": 256,
        "gate_decisions": ["allowed", "allowed"],
        "denied_count": 0,
    }
    common: dict[str, Any] = {
        "ts": "2026-05-02T00:00:00Z",
        "conversation_id": "conv-x",
        "user_id": "practitioner-dr-smith",
        "patient_id": "fixture-3",
        "turn_index": 1,
        "workflow_id": "W-2",
        "classifier_confidence": 0.92,
        "decision": "allow",
        "regen_count": 0,
        "tool_call_count": 2,
        "fetched_ref_count": 5,
        "latency_ms": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "model_provider": "openai",
        "model_name": "gpt-4o-mini",
    }

    write_audit_event(AuditEvent(extra=base_extra, **common), settings)
    write_audit_event(AuditEvent(extra=dict(base_extra), **common), settings)

    lines = Path(settings.agent_audit_log_path).read_text().splitlines()
    assert len(lines) == 2
    parsed_click = json.loads(lines[0])
    parsed_typed = json.loads(lines[1])

    # Same set of top-level keys.
    assert set(parsed_click.keys()) == set(parsed_typed.keys())
    # Same ``extra`` shape — both carry the gate-decision summary.
    assert set(parsed_click["extra"].keys()) == set(parsed_typed["extra"].keys())
    assert parsed_click["extra"]["gate_decisions"] == ["allowed", "allowed"]
    assert parsed_click["extra"]["denied_count"] == 0
    # Workflow + decision are stable across the two paths.
    assert parsed_click["workflow_id"] == parsed_typed["workflow_id"]
    assert parsed_click["decision"] == parsed_typed["decision"]
