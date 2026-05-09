# OpenEMR Clinical Co-Pilot — Agent Service

LangGraph + FastAPI service backing the SMART-on-FHIR Co-Pilot described in
[`../ARCHITECTURE.md`](../ARCHITECTURE.md).

**Graph today:**
`classifier → {clarify | agent | triage} → verifier → {regen ↺ | END}`

* `classifier` — structured-output workflow router (W-1..W-11 + `unclear`).
* `clarify` — disambiguating question when classifier confidence is below the
  threshold.
* `agent` — UC-2 per-patient brief; binds the active SMART patient context
  into the tool layer (defense in depth, ARCHITECTURE.md §7).
* `triage` — UC-1 cross-panel ranking; clears patient context so probes can
  fan out across the user's care team.
* `verifier` — deterministic citation-resolution check (§13). Up to two
  regenerations on unsourced citations before refusing.

Each terminal node emits a structured **block** matching the wire contract in
[`../agentforge-docs/CHAT-API-CONTRACT.md`](../agentforge-docs/CHAT-API-CONTRACT.md):

| Node               | Block kind   |
| ------------------ | ------------ |
| `clarify`          | `plain`      |
| `agent` (W-2)      | `overnight`  |
| `triage` (W-1)     | `triage`     |
| `verifier` refusal | `plain`      |

Schemas live in [`src/copilot/api/schemas.py`](src/copilot/api/schemas.py)
(Pydantic v2, frozen, discriminator on `kind`). Free-text `<cite ref="..."/>`
tags emitted by the synthesis prompt are ratified against `fetched_refs` and
then converted into structured `Citation` objects pointing at the OpenEMR
chart card the frontend should highlight.

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

The contract suite (`test_chat_contract.py`) exercises every block variant,
the discriminator routing, and the citation-card mapping with no LLM and
no network calls. The graph-smoke and audit suites cover compilation and
the per-turn JSONL writer.

## Install the pre-push eval gate

From the repository root:

```bash
cp hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push
```

`hooks/pre-push` is the committed hook wrapper. It delegates to the top-level
`scripts/eval-gate-prepush.sh` script, which owns changed-file detection and the
fixture-based W2 gate commands.

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

Sample chat request (fixture mode):

```bash
curl -sS http://localhost:8000/chat \
  -H 'content-type: application/json' \
  -d '{
        "conversation_id": "demo-1",
        "patient_id": "fixture-1",
        "user_id": "naama",
        "smart_access_token": "",
        "message": "What happened to Eduardo overnight?"
      }'
```

Response shape (abridged) — see
[`../agentforge-docs/CHAT-API-CONTRACT.md`](../agentforge-docs/CHAT-API-CONTRACT.md)
for the full contract:

```json
{
  "conversation_id": "demo-1",
  "reply": "Eduardo Perez had a hypotensive episode at 03:14 with full recovery by 04:00.",
  "block": {
    "kind": "overnight",
    "lead": "Eduardo Perez had a hypotensive episode at 03:14 with full recovery by 04:00.",
    "deltas": [{"label": "BP", "from": "138/82", "to": "90/60", "dir": "down"}],
    "timeline": [
      {"t": "03:14", "kind": "Vital", "text": "BP 90/60", "fhir_ref": "Observation/obs-bp-2"}
    ],
    "citations": [
      {"card": "vitals", "label": "Observation (vitals)", "fhir_ref": "Observation/obs-bp-2"}
    ],
    "followups": ["Suggest next orders", "Show last night's vitals trend", "Draft progress note"]
  },
  "state": {
    "patient_id": "fixture-1",
    "workflow_id": "W-2",
    "classifier_confidence": 0.93,
    "message_count": 2
  }
}
```

## Layout

```
agent/
├── pyproject.toml          # uv-managed, py3.12
├── src/copilot/
│   ├── config.py           # Settings (pydantic-settings; ALLOWED_ORIGINS, etc.)
│   ├── state.py            # CoPilotState — messages, bindings, block, fetched_refs
│   ├── tools.py            # 9 FHIR tools with patient-context enforcement
│   ├── fhir.py             # FhirClient (real or fixture)
│   ├── fixtures.py         # Synthetic 5-patient panel for dev/eval
│   ├── checkpointer.py     # MemorySaver default, PostgresSaver via extra
│   ├── prompts.py          # Synthesis prompts (CLASSIFIER, PER_PATIENT_BRIEF, TRIAGE_BRIEF)
│   ├── graph.py            # build_graph() — classifier → {clarify|agent|triage} → verifier
│   ├── blocks.py           # Synthesis-text → Block conversion (Option A: with_structured_output)
│   ├── api/                # Wire-format DTOs (Pydantic v2)
│   │   └── schemas.py      # ChatRequest/ChatResponse/Block + citation helpers
│   ├── audit.py            # Per-turn JSONL audit log
│   ├── observability.py    # Langfuse callback handler
│   └── server.py           # FastAPI app
└── tests/
    ├── test_graph_smoke.py
    ├── test_audit.py
    ├── test_chat_contract.py     # NEW — wire-shape contract tests
    └── test_patient_context_guard.py
```

## What's stubbed

- `/smart/launch` and `/smart/callback` perform a skeletal PKCE exchange but
  the production token cache is not yet bound to chat sessions; the chat
  endpoint accepts an explicit `patient_id` body for fixture/dev runs.
- `_assert_patient_context_matches` in `server.py` is a typed placeholder
  for the cross-layer SMART-context guard (HTTP 403 above the tool layer);
  it currently always passes.

## Common commands

```bash
uv run pytest -q                                  # tests
uv run ruff check src tests                       # lint
uv run ruff format src tests                      # format
uv run uvicorn copilot.server:app --reload        # dev server
```
