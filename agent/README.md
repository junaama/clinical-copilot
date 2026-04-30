# OpenEMR Clinical Co-Pilot — Agent Service

LangGraph + FastAPI service backing the SMART-on-FHIR Co-Pilot described in
[`../ARCHITECTURE.md`](../ARCHITECTURE.md). Skeleton today: `START → echo → END`
with an in-memory checkpointer, plus stub SMART launch endpoints.

## Requirements

- Python 3.12 (`.python-version` pins it; `uv` will fetch it if needed)
- [`uv`](https://github.com/astral-sh/uv) 0.5+
- An `ANTHROPIC_API_KEY` (only required once the LLM nodes land)

## Install

```bash
cd agent
uv sync --extra dev
```

To enable the Postgres checkpointer (production path), add the optional extra:

```bash
uv sync --extra dev --extra postgres
```

## Configure

```bash
cp .env.example .env
# fill in ANTHROPIC_API_KEY, SMART_CLIENT_ID/SECRET when registered, etc.
```

Runtime settings live in `src/copilot/config.py` (pydantic-settings, env-driven).
Leave `CHECKPOINTER_DSN` empty to use the in-memory saver; set it to a Postgres
DSN to switch to durable conversation state.

## Run the test suite

```bash
uv run pytest -q
```

The smoke suite exercises graph compilation, the echo node, and checkpointer
persistence across invocations — no network calls, no API keys required.

## Run the service locally

```bash
uv run uvicorn copilot.server:app --reload --port 8000
```

Then:

| Method | Path                | Purpose                                      |
| ------ | ------------------- | -------------------------------------------- |
| GET    | `/health`           | Liveness probe                               |
| POST   | `/chat`             | Send a turn through the graph                |
| GET    | `/smart/launch`     | SMART EHR launch entry point (stub)          |
| GET    | `/smart/callback`   | OAuth2 redirect target (stub)                |
| GET    | `/docs`             | Interactive OpenAPI UI                       |

Sample chat request:

```bash
curl -sS http://localhost:8000/chat \
  -H 'content-type: application/json' \
  -d '{
        "conversation_id": "demo-1",
        "patient_id": "4",
        "user_id": "naama",
        "smart_access_token": "stub",
        "message": "What happened to Eduardo overnight?"
      }'
```

## Layout

```
agent/
├── pyproject.toml          # uv-managed, py3.12
├── .python-version
├── .env.example
├── src/copilot/
│   ├── config.py           # Settings (pydantic-settings)
│   ├── state.py            # CoPilotState (messages reducer + bindings)
│   ├── tools.py            # ToolResult/Row + get_patient_demographics stub
│   ├── checkpointer.py     # MemorySaver default, PostgresSaver via extra
│   ├── graph.py            # build_graph() — single echo node today
│   └── server.py           # FastAPI app
└── tests/
    └── test_graph_smoke.py
```

## What's stubbed

- `echo_node` stands in for the §9 pipeline (classifier → planner → tool
  dispatch → synthesis → verifier).
- `/smart/launch` and `/smart/callback` validate inputs but do not yet perform
  the OAuth2 PKCE flow.
- Only `get_patient_demographics` is implemented; the other 11 tools from §8
  follow.

## Common commands

```bash
uv run pytest -q                                  # tests
uv run ruff check src tests                       # lint
uv run ruff format src tests                      # format
uv run uvicorn copilot.server:app --reload        # dev server
```
