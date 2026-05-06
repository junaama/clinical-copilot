# `agent/tests/`

Pytest-discovered tests for the Co-Pilot agent. Run from `agent/`:

```bash
uv run pytest -q
```

Two layers exist; pick the right one for what you're checking.

## Unit / fixture tests (`test_*.py`, except `test_graph_integration.py`)

Hundreds of small tests covering individual modules — schemas, helpers,
tool wiring, node behaviour in isolation, the supervisor as a single
node, etc. Each test stubs out everything except the unit under test.
This is the right place to add coverage for new pure logic, schema
shapes, or single-node contracts.

## Graph integration layer (`test_graph_integration.py`)

A small suite that builds the **full** LangGraph via `build_graph(...)`
and runs end-to-end transcripts. External I/O is stubbed at the boundary
(chat model, `create_agent`, the block synthesizer); the graph itself
runs unmocked.

This layer exists because the node-isolated W2 fixture eval suite
(`copilot.eval.w2_runner`) cannot catch wiring regressions by
construction — it pokes nodes individually and never exercises the
state→contextvar handoff, supervisor re-dispatch behaviour, the
classifier-message-stream contract, or the verifier-precondition shape
that workers must produce.

The four 2026-05-06 production bugs that motivated this suite were each
a one-line change in graph wiring; together they cost a half-day of
round-trip debugging in production. Each bug now has a regression test
in this file:

| Bug | Symptom | Regression test |
|-----|---------|-----------------|
| Worker contextvars not bound | Tools see `no_active_user` / `no_token` after supervisor dispatch | `test_worker_binds_state_to_tool_layer_contextvars` |
| Supervisor re-dispatch loop | Worker runs MAX_SUPERVISOR_ITERATIONS times; turn ends on a `ToolMessage` | `test_w_evd_synthesizes_after_one_dispatch` |
| Classifier blind to upload sentinel | Post-upload turns mis-route to W-2 instead of W-DOC | `test_w_doc_routes_when_upload_sentinel_present` |
| Worker stops on `ToolMessage` | Verifier refuses with "I couldn't produce a verifiable response" | `test_worker_ending_on_toolmessage_synthesizes_aimessage_fallback` |

Plus one defence-in-depth case (`test_w1_routes_to_agent_node_and_binds_contextvars`)
that pins the agent_node path against false-positive supervisor routing
and exercises the agent_node contextvar handoff.

The whole suite runs in well under a second and is wired into the W2
pre-push gate (`scripts/eval-gate-prepush.sh`) so a graph-wiring
regression blocks `git push` on the same trigger as the W2 eval gate.

## Live end-to-end suite (`test_graph_e2e_live.py`, marker: `live`)

This is the answer to "but does any of that actually call a model?".
The integration suite stubs the chat model, ``create_agent``, and the
synthesizer — that's how 5 transcripts run in 0.45 s. The **live** suite
makes real calls instead:

* real OpenAI / Anthropic for the classifier and supervisor LLMs
* real Cohere ``embed-english-v3.0`` + ``rerank-english-v3.0``
* real Anthropic Claude vision for VLM extraction
* real Postgres (pgvector + ``document_extractions``)
* real OpenEMR Standard API for document upload

Cases:

| Test | What it exercises end-to-end |
|---|---|
| `test_e2e_evidence_path_against_real_corpus` | classifier → supervisor → `evidence_retriever` → real Cohere retrieval against the indexed guideline corpus → real synthesis → verifier allows |
| `test_e2e_upload_then_extract_lab_pdf` | DocumentClient hits real OpenEMR Standard API → sentinel injected → classifier picks W-DOC → supervisor dispatches `intake_extractor` → real `extract_document` (VLM + bbox + persistence) → verifier allows |
| `test_e2e_mixed_chart_and_guideline_prefers_evidence` | Pins the routing rule from `prompts.py:60-61` (mixed chart+guideline → W-EVD) against the real classifier, so a future model bump can't silently regress it |

Cost: ~$0.05–$0.20 per run depending on case mix. Wall-clock: 20–60 s.

The suite is **opt-in**. The default `addopts` in `pyproject.toml` filters
out `-m live` so the unit + integration layers stay sub-second and the
pre-push gate doesn't burn API credit. To run:

```bash
cd agent
uv run pytest -m live -v tests/test_graph_e2e_live.py
```

Tests that find missing API keys / DSNs `skip`, not fail — the suite
must run cleanly on a fresh laptop with no credentials.

### Required env

`OPENAI_API_KEY` (if `LLM_PROVIDER=openai`), `ANTHROPIC_API_KEY`,
`COHERE_API_KEY`, `CHECKPOINTER_DSN` (with pgvector + W2 tables migrated +
guideline corpus indexed), `OPENEMR_FHIR_TOKEN`,
`COPILOT_ADMIN_USER_IDS`. Optional: `E2E_PATIENT_UUID` if the default
admin uuid isn't a real patient on your OpenEMR instance.

## When to add a test where

| Question | Where it lives |
|---|---|
| **What** a single node decides given specific inputs | unit test (`tests/test_<module>.py`) or `w2_runner` fixture case (`evals/`) |
| **How** the graph hands data between nodes (state→contextvar, message-stream contract, post-worker re-dispatch, citation-subset invariant on the terminal AIMessage) | `tests/test_graph_integration.py` |
| End-to-end with real models, real Cohere, real OpenEMR — verifying the wiring against actual cognition | `tests/test_graph_e2e_live.py` (mark `live`) |
| LLM behaviour quality on real prompts (faithfulness, refusal phrasing, citation discipline at scale) | eval harness (`make eval-full`), not pytest |

Don't merge the suites. The fixture eval gate is deliberately
node-isolated to stay sub-second; integration tests need the full graph
and live in `tests/`, not `evals/`; live e2e is opt-in to avoid burning
API credits on every push.
