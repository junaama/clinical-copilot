## Parent PRD

`issues/w2-mvp-prd.md` (post-MVP follow-up — surfaced from production debugging on 2026-05-06)

## Why

The W2 fixture eval gate (issue 010) exercises individual nodes in isolation via `copilot.eval.w2_runner`, not the wired LangGraph from `build_graph`. Four production bugs landed during the 2026-05-06 demo prep that the unit-level eval suite could not have caught because all of them are integration-level wiring bugs at the graph layer:

1. **Worker contextvars not bound** — `_run_worker` invoked the sub-agent without calling `set_active_smart_token` / `set_active_user_id` / `set_active_registry`, so `retrieve_evidence` and the FHIR/Document clients all returned `no_active_user` / `no_token`. `agent_node` does the binding at `graph.py:471-473`; workers were missing it. Eval runner sets contextvars by hand, never exercises the state→contextvar handoff.

2. **Supervisor re-dispatch loop** — `supervisor_node` only consulted the user's latest `HumanMessage` to make its decision, ignoring `state["tool_results"]` and `state["fetched_refs"]`. After a worker ran, the supervisor LLM saw the same user message and re-dispatched the same worker until `MAX_SUPERVISOR_ITERATIONS`, ending the turn on a `ToolMessage`. Eval runner never invokes the supervisor twice on one turn.

3. **Classifier blind to upload sentinel** — `classifier_node` filtered `state["messages"]` to `HumanMessage` only, dropping the `[system] Document uploaded: …` sentinel that `prompts.py:55` says **must** route to W-DOC. Post-upload turns mis-routed to W-2 brief. Eval runner injects synthetic `workflow_id` directly into state, never exercises the classifier-message-stream contract.

4. **Worker stops on `ToolMessage`** — when `create_agent` hit a recursion limit or stopped without a final synthesis, `_run_worker` returned that `ToolMessage` as `final`. The verifier's `not isinstance(last, AIMessage)` branch fired and the user saw "I couldn't produce a verifiable response." Eval runner asserts on tool calls, not on the message-list shape the verifier reads.

Every fix shipped today was a one-or-two-line change in graph wiring. The cost was a half-day of round-trip debugging in production and four redeploys. A single integration test fixture would have caught all four.

## What to build

Add `agent/tests/test_graph_integration.py` (and any supporting fixtures) that:

1. **Builds the full graph via `build_graph(settings, checkpointer=...)`** — same wiring as the production `/chat` endpoint. No node-by-node mocking; node mocking misses wiring bugs by definition.
2. **Stubs only external IO at the boundary** — Cohere embed/rerank, Anthropic VLM, OpenEMR FHIR/Standard API. Use the existing `EmbeddingFn` / `SqlFn` / `RerankFn` injection seams in `retrieval/retriever.py` plus existing test doubles for FHIR/Document clients. The graph itself runs unmocked.
3. **Covers four end-to-end transcripts at minimum** — one per integration bug class hit on 2026-05-06:
   - **W-EVD**: user asks *"What does ADA say about A1c targets?"* → classifier picks W-EVD → supervisor dispatches `evidence_retriever` → tool returns canned chunks with `guideline_ref` → supervisor synthesizes (does NOT re-dispatch) → verifier passes citations against `fetched_refs` → AIMessage returned.
   - **W-DOC after upload**: state seeded with `[system] Document uploaded: lab_pdf "x.pdf" (document_id: …) for Patient/…` SystemMessage, user says *"walk me through what's notable"* → classifier picks W-DOC (sees the sentinel) → supervisor dispatches `intake_extractor` → no re-dispatch loop → AIMessage with `DocumentReference/...` citations.
   - **W-1 unchanged**: a panel-triage user message routes to `agent_node`, not the supervisor — guards against false-positive supervisor routing.
   - **Worker ends on ToolMessage**: stub `create_agent` to return a sub-message list ending in `ToolMessage` → verify `_run_worker` falls back to a synthesized `AIMessage` so verifier doesn't refuse the turn.

4. **Asserts on the structural invariants the existing unit tests miss:**
   - `state["smart_access_token"]` and `state["user_id"]` are bound to the contextvars seen by the tool layer mid-run (assertion via a fake tool that records `get_active_*()` snapshots).
   - `supervisor_iterations` does not exceed `MAX_SUPERVISOR_ITERATIONS - 1` for a normal happy path.
   - The terminal message in `result["messages"]` is always an `AIMessage` (verifier's precondition).
   - `fetched_refs` non-empty whenever a worker ran tools that emit `*_ref` payloads.
   - Citation refs in the final AIMessage are a subset of `fetched_refs` (verifier's gate).

5. **Runs in the existing `pytest` invocation** — wire into `pytest -q` and into the W2 pre-push gate trigger so CI catches integration regressions on the same trigger as the unit suite.

## Acceptance criteria

- [ ] `tests/test_graph_integration.py` exists with the four happy-path transcripts above
- [ ] Each transcript invokes `build_graph(...).ainvoke(...)` with stub IO, not isolated nodes
- [ ] Asserts contextvar binding at the tool layer, supervisor iteration budget, terminal `AIMessage`, and citation-subset invariant
- [ ] Suite runs in <5 seconds (no live LLM/embedding/FHIR calls)
- [ ] Each of the four 2026-05-06 production bugs has a regression test that fails without its fix and passes with it (red/green confirmation in the PR)
- [ ] Wired into `pytest` default discovery and into the pre-push hook's trigger globs (`agent/src/`, `agent/tests/`)
- [ ] Documented in `agent/tests/README.md` (or equivalent) with a one-paragraph "what this layer catches that w2_runner misses"

## Out of scope

- Live LLM calls (covered by `make eval-full`).
- New behavioural eval cases (covered by issue 010's 50-case suite).
- Performance / latency budgets (separate concern).

## Blocked by

None — the four bugs are already fixed in `agent/src/copilot/{graph.py,supervisor/graph.py,supervisor/workers.py}`. This issue documents the test layer those fixes deserve.

## Notes

- The 50-case eval (`copilot.eval.w2_runner`) is **not** the right place for these checks: it's deliberately fixture-based and node-isolated so the pre-push gate stays sub-second. Integration tests are slower and live in `tests/`, not `evals/`. Don't merge the two suites.
- Consider extending `agent/tests/conftest.py` with a shared `build_test_graph` fixture so future integration tests don't each re-stub the IO surface from scratch.
