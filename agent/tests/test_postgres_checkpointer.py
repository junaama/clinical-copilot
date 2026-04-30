"""Postgres checkpointer integration test.

Skipped when ``COPILOT_TEST_PG_DSN`` isn't set (the default in CI / local
dev). Run manually after starting a disposable Postgres:

    docker run --rm -d --name copilot-pg -p 5442:5432 \\
      -e POSTGRES_PASSWORD=postgres -e POSTGRES_USER=postgres \\
      -e POSTGRES_DB=postgres postgres:16-alpine

    COPILOT_TEST_PG_DSN='postgresql://postgres:postgres@localhost:5442/postgres?sslmode=disable' \\
      uv run pytest tests/test_postgres_checkpointer.py -v
"""

from __future__ import annotations

import os

import pytest
from langchain_core.messages import HumanMessage

from copilot.checkpointer import open_checkpointer
from copilot.config import Settings
from copilot.graph import build_graph

_DSN = os.environ.get("COPILOT_TEST_PG_DSN", "")

pytestmark = pytest.mark.skipif(
    not _DSN, reason="set COPILOT_TEST_PG_DSN to run this integration test"
)


async def test_postgres_checkpointer_persists_across_invocations() -> None:
    # Settings reads OPENAI_API_KEY from agent/.env via pydantic-settings.
    settings = Settings(
        LLM_PROVIDER="openai",
        USE_FIXTURE_FHIR=True,
        CHECKPOINTER_DSN=_DSN,
    )

    thread_id = "pg-integration-1"
    config = {"configurable": {"thread_id": thread_id}}

    async with open_checkpointer(settings) as checkpointer:
        graph_a = build_graph(settings, checkpointer=checkpointer)
        await graph_a.ainvoke(
            {
                "messages": [HumanMessage(content="What happened to this patient?")],
                "conversation_id": thread_id,
                "patient_id": "fixture-1",
                "user_id": "naama",
            },
            config=config,
        )
        snapshot = await graph_a.aget_state(config)
        assert snapshot.values.get("patient_id") == "fixture-1"
        # At least one Human + one AI message survived.
        assert len(snapshot.values.get("messages", [])) >= 2


async def test_postgres_checkpointer_state_survives_new_graph_instance() -> None:
    """Open a saver, write state, close it, reopen it, read state back."""
    # Settings reads OPENAI_API_KEY from agent/.env via pydantic-settings.
    settings = Settings(
        LLM_PROVIDER="openai",
        USE_FIXTURE_FHIR=True,
        CHECKPOINTER_DSN=_DSN,
    )

    thread_id = "pg-integration-2"
    config = {"configurable": {"thread_id": thread_id}}

    # First session: write.
    async with open_checkpointer(settings) as cp1:
        graph_1 = build_graph(settings, checkpointer=cp1)
        await graph_1.ainvoke(
            {
                "messages": [HumanMessage(content="Active medications?")],
                "conversation_id": thread_id,
                "patient_id": "fixture-1",
                "user_id": "naama",
            },
            config=config,
        )

    # Second session: read state from the same DSN, expect prior turn visible.
    async with open_checkpointer(settings) as cp2:
        graph_2 = build_graph(settings, checkpointer=cp2)
        snapshot = await graph_2.aget_state(config)
        assert snapshot.values.get("patient_id") == "fixture-1"
        humans = [
            m for m in snapshot.values.get("messages", [])
            if isinstance(m, HumanMessage)
        ]
        assert any("medications" in m.content.lower() for m in humans), (
            "prior turn should be visible after reopening the saver"
        )
