"""ConversationTurnRegistry — per-turn provenance store (issue 045).

Exercises the registry through its public interface using the in-memory
backend so tests run without Postgres. Covers:

- ``append_turn`` allocates monotonically-increasing turn indices per
  conversation and persists structured ``block`` + route metadata.
- ``list_turns`` returns rows in turn-index order.
- Per-conversation isolation: writes for one id don't show up under another.
- Empty-state and unknown-id reads return an empty list cleanly.

Prior art: ``test_conversations.py`` for the in-memory registry pattern.
"""

from __future__ import annotations

from copilot.conversation_turns import (
    ConversationTurnRegistry,
    InMemoryTurnStore,
)


def _registry() -> ConversationTurnRegistry:
    return ConversationTurnRegistry(store=InMemoryTurnStore())


# ---------- append_turn ----------


async def test_append_turn_starts_at_index_zero() -> None:
    reg = _registry()
    turn = await reg.append_turn(
        conversation_id="conv-a",
        user_message="hi",
        assistant_text="hello",
        block={"kind": "plain", "lead": "hello", "citations": [], "followups": []},
        route_kind="chart",
        route_label="Reading the patient record",
    )
    assert turn.turn_index == 0


async def test_append_turn_increments_index_per_conversation() -> None:
    reg = _registry()
    for i in range(3):
        await reg.append_turn(
            conversation_id="conv-a",
            user_message=f"q{i}",
            assistant_text=f"a{i}",
            block={"kind": "plain", "lead": f"a{i}", "citations": [], "followups": []},
            route_kind="chart",
            route_label="Reading the patient record",
        )
    turns = await reg.list_turns("conv-a")
    assert [t.turn_index for t in turns] == [0, 1, 2]


async def test_append_turn_persists_structured_block() -> None:
    """The structured block survives the round trip — that's the whole point
    of the provenance store. A triage block with cohort rows reloads with
    the cohort intact."""
    reg = _registry()
    triage_block = {
        "kind": "triage",
        "lead": "Three patients need attention",
        "cohort": [
            {
                "id": "pat-1",
                "name": "Robert Hayes",
                "age": 67,
                "room": "302",
                "score": 88,
                "trend": "up",
                "reasons": ["RR up", "SpO2 down"],
                "self": False,
                "fhir_ref": "Patient/pat-1",
            },
        ],
        "citations": [],
        "followups": [],
    }
    await reg.append_turn(
        conversation_id="conv-a",
        user_message="who needs me?",
        assistant_text="Three patients need attention",
        block=triage_block,
        route_kind="panel",
        route_label="Reviewing your panel",
        workflow_id="W-1",
        classifier_confidence=0.85,
        decision="allow",
        supervisor_action="run_panel_triage",
    )
    turns = await reg.list_turns("conv-a")
    assert len(turns) == 1
    t = turns[0]
    assert t.block["kind"] == "triage"
    assert t.block["cohort"][0]["name"] == "Robert Hayes"
    assert t.route_kind == "panel"
    assert t.route_label == "Reviewing your panel"
    assert t.workflow_id == "W-1"
    assert t.classifier_confidence == 0.85
    assert t.decision == "allow"
    assert t.supervisor_action == "run_panel_triage"


async def test_append_turn_preserves_citations_inside_block() -> None:
    """Source chips are derived from ``block.citations``; reload must not
    drop them. AC: rehydration restores source chips for cited answers."""
    reg = _registry()
    block = {
        "kind": "plain",
        "lead": "Robert is on lisinopril.",
        "citations": [
            {
                "card": "medications",
                "label": "Lisinopril 10mg",
                "fhir_ref": "MedicationRequest/mr-1",
            },
            {
                "card": "guideline",
                "label": "JNC8 §3",
                "fhir_ref": "guideline:jnc8-3",
            },
        ],
        "followups": ["What about his BP trend?"],
    }
    await reg.append_turn(
        conversation_id="conv-a",
        user_message="meds for Robert?",
        assistant_text="Robert is on lisinopril.",
        block=block,
        route_kind="chart",
        route_label="Reading the patient record",
    )
    turns = await reg.list_turns("conv-a")
    assert len(turns[0].block["citations"]) == 2
    assert turns[0].block["citations"][0]["fhir_ref"] == "MedicationRequest/mr-1"
    assert turns[0].block["citations"][1]["fhir_ref"] == "guideline:jnc8-3"


async def test_list_turns_isolated_per_conversation() -> None:
    reg = _registry()
    await reg.append_turn(
        conversation_id="conv-a",
        user_message="qa",
        assistant_text="aa",
        block={"kind": "plain", "lead": "aa", "citations": [], "followups": []},
        route_kind="chart",
        route_label="Reading the patient record",
    )
    await reg.append_turn(
        conversation_id="conv-b",
        user_message="qb",
        assistant_text="ab",
        block={"kind": "plain", "lead": "ab", "citations": [], "followups": []},
        route_kind="chart",
        route_label="Reading the patient record",
    )
    a_turns = await reg.list_turns("conv-a")
    b_turns = await reg.list_turns("conv-b")
    assert [t.user_message for t in a_turns] == ["qa"]
    assert [t.user_message for t in b_turns] == ["qb"]


async def test_list_turns_unknown_id_returns_empty() -> None:
    reg = _registry()
    turns = await reg.list_turns("never-created")
    assert turns == []


async def test_append_turn_with_refusal_block_route() -> None:
    """A refusal turn (``kind: refusal`` route) must round-trip the route
    metadata so the badge survives reopen — including the Panel-failure
    exception where a W-1 refusal still advertises the panel route."""
    reg = _registry()
    await reg.append_turn(
        conversation_id="conv-a",
        user_message="who needs me?",
        assistant_text="Panel data is unavailable right now…",
        block={
            "kind": "plain",
            "lead": "Panel data is unavailable right now…",
            "citations": [],
            "followups": [],
        },
        route_kind="panel",
        route_label="Panel data unavailable",
        workflow_id="W-1",
        decision="tool_failure",
    )
    turns = await reg.list_turns("conv-a")
    assert turns[0].route_kind == "panel"
    assert turns[0].route_label == "Panel data unavailable"
    assert turns[0].decision == "tool_failure"
