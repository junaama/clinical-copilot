"""Run a single UC-2 query end-to-end against the fixture patient.

Usage:
    OPENAI_API_KEY=sk-... uv run python -m copilot.run_query
    OPENAI_API_KEY=sk-... uv run python -m copilot.run_query "your question"

Streams tool calls + final response to stdout. Uses the synthetic fixture
patient by default; set ``USE_FIXTURE_FHIR=0`` and ``OPENEMR_FHIR_TOKEN`` to
hit real OpenEMR.
"""

from __future__ import annotations

import asyncio
import os
import sys

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from .config import get_settings
from .fixtures import PATIENT_ID, PRACTITIONER_DR_SMITH
from .llm import build_chat_model
from .observability import get_callback_handler
from .prompts import build_system_prompt
from .tools import make_tools, set_active_registry, set_active_user_id

DEFAULT_QUERY = "What happened to this patient since I last saw them?"


async def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUERY

    settings = get_settings()
    if settings.llm_provider == "openai" and not settings.openai_api_key.get_secret_value():
        print("error: OPENAI_API_KEY not set", file=sys.stderr)
        return 2
    if settings.llm_provider == "anthropic" and not settings.anthropic_api_key.get_secret_value():
        print("error: ANTHROPIC_API_KEY not set", file=sys.stderr)
        return 2

    chat_model = build_chat_model(settings)
    tools = make_tools(settings)

    patient_id = os.environ.get("PATIENT_ID", PATIENT_ID)
    # Bind a fixture practitioner so the CareTeam gate authorizes panel
    # patients during the dev script's tool calls. ``run_query`` is a
    # fixture-mode utility; in production the agent is invoked through the
    # graph, which binds ``user_id`` from session state.
    set_active_user_id(PRACTITIONER_DR_SMITH)
    set_active_registry({})
    agent = create_agent(
        model=chat_model,
        tools=tools,
        system_prompt=build_system_prompt(
            registry={},
            focus_pid=None,
            workflow_id="W-2",
            confidence=1.0,
        ),
    )
    user = HumanMessage(content=query)

    print(f"\n=== query for patient {patient_id} ===")
    print(f"  {query!r}\n")
    print(f"=== provider={settings.llm_provider} model={settings.llm_model} ===\n")

    invoke_config = {}
    handler = get_callback_handler(settings)
    if handler is not None:
        invoke_config["callbacks"] = [handler]
    result = await agent.ainvoke({"messages": [user]}, config=invoke_config)

    print("=== trace ===")
    for msg in result["messages"]:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                args = ", ".join(f"{k}={v!r}" for k, v in (tc.get("args") or {}).items())
                print(f"  → tool: {tc['name']}({args})")
        elif isinstance(msg, ToolMessage):
            preview = (msg.content or "")[:120].replace("\n", " ")
            print(f"  ← {msg.name}: {preview}{'...' if len(msg.content or '') > 120 else ''}")
        elif isinstance(msg, AIMessage):
            print("\n=== response ===")
            print(msg.content)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
