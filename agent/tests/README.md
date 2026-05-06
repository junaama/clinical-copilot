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

### When to add a test here vs to `w2_runner`

* If you're testing **what** a node decides given specific inputs — node
  unit test or `w2_runner` fixture case.
* If you're testing **how** the graph hands data between nodes
  (state→contextvar, message-stream contract, post-worker re-dispatch,
  citation-subset invariant on the terminal AIMessage) — `test_graph_integration.py`.
* If you're testing the LLM's behaviour on real prompts — eval harness
  (`make eval-full`), not pytest.

Don't merge the two suites. The fixture eval gate is deliberately
node-isolated to stay sub-second; integration tests need the full graph
and live in `tests/`, not `evals/`.
