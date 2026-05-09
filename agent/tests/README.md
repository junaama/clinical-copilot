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
pre-push gate (`hooks/pre-push`, delegating to
`scripts/eval-gate-prepush.sh`) so a graph-wiring
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

## HTTP-level e2e against the deployed agent (`test_http_e2e_deployed.py`, marker: `live_http`)

A second live tier that talks to the **deployed** agent over HTTPS using a
session cookie captured from a manual browser login. This sidesteps the
in-process `live` suite's blockers (no local Postgres, no static
`OPENEMR_FHIR_TOKEN` — the deployed agent uses dynamic SMART tokens) and
verifies the post-issue-022 (id recovery) and post-issue-023 (cache-first
extraction) fixes hold in production.

One case:

| Test | What it exercises end-to-end |
|---|---|
| `test_deployed_upload_then_chat_then_cached_chat` | `POST /conversations` → `POST /upload` (lab PDF, real VLM call) → `POST /chat` (notable-findings walk-through cites the canonical `DocumentReference/<id>`) → `POST /chat` (second turn surfaces non-empty `state.cache_hits`, proving the second extract was cache-served) |

Cost: ~$0.10 per run — exactly one VLM call (the upload's first
extraction); both chat turns sit on the cache after that. Wall-clock:
30-90 s.

The suite is **opt-in**. Default `addopts` in `pyproject.toml` filters
out `-m live_http`. To run:

```bash
cd agent
COPILOT_SESSION_COOKIE=<value-from-browser> \
  uv run pytest -m live_http -v tests/test_http_e2e_deployed.py
```

A missing cookie causes `skip`, never `fail`.

### Required env

| Var | Purpose |
|---|---|
| `COPILOT_SESSION_COOKIE` | value of the `copilot_session` cookie from a successful manual login. The `COPILOT_TEST_SESSION_TOKEN` alias is also accepted. |
| `COPILOT_AGENT_BASE_URL` | deployed agent base URL. Defaults to the Railway prod URL. |
| `E2E_PATIENT_UUID` | optional override for the upload patient. The `E2E_LIVE_HTTP_PATIENT_UUID` alias is accepted to disambiguate from the in-process `live` suite. Falls back to the first patient on the session's `/panel` roster. |

### Reverting the underlying fixes

The case is also a regression detector. Revert
[022](../../issues/done/022-doc-id-recovery-in-upload-flow.md) and the
upload assertion fails (synthetic `openemr-upload-` prefix back in the
response). Revert
[023](../../issues/done/023-extraction-cache-first-and-regression-coverage.md)
and the second-chat-turn assertion fails (`cache_hits` stays empty
because the second `extract_document` ran a fresh VLM extraction).

## Week 2 reliability live smoke (`test_w2_reliability_live_smoke.py`, marker: `live_http`)

A second `live_http` file, narrower than `test_http_e2e_deployed.py` and
focused on the Week 2 reliability slice (issues 024-028). It pins the
exact failures the deployed browser flow surfaced before those fixes
landed: silent wrong-type uploads, panel/chat contradiction on the
post-upload turn, and uncited guideline answers from RAG.

Cases:

| Test | What it exercises end-to-end |
|---|---|
| `test_smoke_lab_mode_rejects_intake_fixture` | `POST /upload` with the intake fixture and `doc_type=lab_pdf` returns HTTP 409 with `detail.code == "doc_type_mismatch"` (issue 024). |
| `test_smoke_intake_upload_then_chat_cites_same_document` | Successful intake upload returns a populated `intake` payload + canonical doc-ref (issue 025); the immediate post-upload chat turn cites the same `DocumentReference/<id>` (issue 026). |
| `test_smoke_ada_a1c_question_returns_guideline_citation` | An ADA A1c-target prompt produces a chat block whose citations include a `guideline`-card record (issues 027 + 028). |
| `test_smoke_kdigo_ace_arb_question_returns_guideline_citation` | A KDIGO ACE/ARB prompt produces the same guideline-card citation shape — sister case for the second corpus the demo relies on. |

Cost: ~$0.25 per full run (one VLM upload + four chat turns); the four
cases are independent so a partial run only pays for the cited turns.
Wall-clock: 60-180 s.

The suite is **opt-in**. Default `addopts` filters out `-m live_http`. To
run:

```bash
cd agent
COPILOT_SESSION_COOKIE=<value-from-browser> \
  uv run pytest -m live_http -v tests/test_w2_reliability_live_smoke.py
```

A missing cookie causes `skip`, never `fail`.

### Required env

Same env contract as `test_http_e2e_deployed.py` — see the table in the
section above. Both files honour `COPILOT_SESSION_COOKIE`,
`COPILOT_AGENT_BASE_URL`, and `E2E_PATIENT_UUID` /
`E2E_LIVE_HTTP_PATIENT_UUID`.

### Required fixtures

The intake-upload and mismatch cases use
`example-documents/intake-forms/p01-chen-intake-typed.pdf`, a synthetic
intake PDF committed under `example-documents/`. No real PHI is
involved. Both guideline-citation cases drive the deployed RAG corpus
directly through `/chat` and don't need a local fixture.

### Reverting the underlying fixes

The smoke is a regression detector for the W2 reliability slice. Reverting
[024](../../issues/done/024-upload-type-mismatch-guard.md) makes the
mismatch case fail (the upload accepts the intake fixture as `lab_pdf`
and returns HTTP 200). Reverting
[025](../../issues/done/025-canonical-upload-outcome.md) makes the intake
case fail (`intake` payload missing on a 200 response, or `discussable`
not flipped). Reverting
[026](../../issues/done/026-post-upload-chat-consistency.md) makes the
post-upload chat assertion fail (the synthetic doc-ref leaks back into
``fetched_refs`` and the verifier refuses the turn). Reverting
[027](../../issues/done/027-guideline-citation-wire-contract.md) makes
both ADA/KDIGO assertions fail (guideline citations dropped from the
block). Reverting
[028](../../issues/done/028-rag-citation-fail-closed.md) loosens the
fail-closed gate; one of the guideline cases will eventually surface an
uncited RAG answer — the smoke catches that regression on the first run
that produces an empty citation list.

## When to add a test where

| Question | Where it lives |
|---|---|
| **What** a single node decides given specific inputs | unit test (`tests/test_<module>.py`) or `w2_runner` fixture case (`evals/`) |
| **How** the graph hands data between nodes (state→contextvar, message-stream contract, post-worker re-dispatch, citation-subset invariant on the terminal AIMessage) | `tests/test_graph_integration.py` |
| End-to-end with real models, real Cohere, real OpenEMR — verifying the wiring against actual cognition | `tests/test_graph_e2e_live.py` (mark `live`) |
| End-to-end against the **deployed** agent over HTTPS, using a captured browser session — verifying the production behaviour of upload + chat + cache-first re-read | `tests/test_http_e2e_deployed.py` (mark `live_http`) |
| Live smoke against the **deployed** agent for the W2 reliability slice — upload mismatch, intake handoff, post-upload chat consistency, ADA/KDIGO guideline citations | `tests/test_w2_reliability_live_smoke.py` (mark `live_http`) |
| LLM behaviour quality on real prompts (faithfulness, refusal phrasing, citation discipline at scale) | eval harness (`make eval-full`), not pytest |

Don't merge the suites. The fixture eval gate is deliberately
node-isolated to stay sub-second; integration tests need the full graph
and live in `tests/`, not `evals/`; live e2e is opt-in to avoid burning
API credits on every push.
