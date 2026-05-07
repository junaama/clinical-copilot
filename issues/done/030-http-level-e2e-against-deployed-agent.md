## Parent PRD

Standalone follow-up — not part of any existing PRD. Motivated by a gap
in `issues/prd-doc-id-recovery-and-extraction-cache.md` validation:
fixes 022 (id recovery) and 023 (cache-first extraction) cannot be
exercised by the existing live e2e suite because
`tests/test_graph_e2e_live.py::test_e2e_upload_then_extract_lab_pdf`
gates on `OPENEMR_FHIR_TOKEN` (a static bearer), and the deployed agent
uses dynamic SMART tokens — the var is never populated, so the upload
test always skips.

## What to build

A live e2e test (or a small focused suite) that talks to the deployed
agent **over HTTPS** at the public Railway URL, mirroring what a real
browser session does:

1. Authenticate the test client against the agent's session/cookie
   contract — either by reusing a session token captured from a manual
   browser login (read from an env var, never written to disk by the
   test) or by adding a narrow test-only admin endpoint on the agent
   that mints a session for a fixed practitioner uuid.
2. POST a lab PDF to `/upload` with a `patient_id` belonging to that
   session's panel.
3. Assert the response body contains a real OpenEMR
   `DocumentReference/<id>` — no `openemr-upload-<hex>` synthetic
   prefix. (Verifies fix 022 in production.)
4. POST a follow-up chat turn to `/chat` referencing the just-uploaded
   document; assert the terminal AIMessage cites the same real id and
   reads as a notable-findings walk-through (no apology turn).
5. POST a second chat turn about the same document; assert the
   `extract_document` span in the response telemetry (or a
   purpose-added `cache_hit` counter on the `/chat` response) shows
   the second extract was cache-served. (Verifies fix 023 in
   production.)

This sidesteps every blocker the existing live suite hit: no local
Postgres connection, no static `OPENEMR_FHIR_TOKEN`, no need to rebuild
the LangGraph in-process. The deployed agent talks to its own private
network, so the public TCP proxy is out of the loop.

## Acceptance criteria

- [ ] A new test file under `agent/tests/` (e.g.
      `test_http_e2e_deployed.py`), gated behind a marker (proposal:
      `live_http`) and excluded from default discovery the same way
      `live` is.
- [ ] The test reads its session credential from an env var
      (proposal: `COPILOT_SESSION_COOKIE` or `COPILOT_TEST_SESSION_TOKEN`)
      and skips cleanly when missing — never fails for unconfigured envs.
- [ ] The test reads the deployed agent base URL from an env var
      (proposal: `COPILOT_AGENT_BASE_URL`, default
      `https://copilot-agent-production-3776.up.railway.app`).
- [ ] On a successful upload, the response payload's document id
      contains `DocumentReference/` and does **not** contain
      `openemr-upload-`.
- [ ] On the post-upload chat turn, the terminal AIMessage cites the
      same `DocumentReference/<id>` and the response shape signals
      `decision == "allow"`.
- [ ] On a second chat turn referencing the same document, the
      response carries observable evidence the second extraction was
      cache-served — either via a new `cache_hit: true` field on the
      `/chat` response (small server change), or via a Langfuse trace
      id the test can fetch and assert against.
- [ ] No test artifacts leave PHI on disk: any uploaded fixture is a
      synthetic / sample document already committed under
      `example-documents/`, not a real patient document.
- [ ] `agent/tests/README.md` gains a section documenting the new
      marker, required env vars, expected wall-clock, and cost
      (per-run upload counts toward Anthropic VLM on the first turn
      only by design — a successful run should produce exactly one
      VLM call across the two chat turns plus the upload).
- [ ] Manual verification: the test passes against the live deployed
      agent today; the same test fails (cleanly, with a message that
      points at the regression) if 022's id recovery is reverted or
      023's cache-first branch is reverted.

## Blocked by

None — can start immediately.

(Optional precursor: a small `/chat` response-shape change to expose
`cache_hit` per-tool. Not strictly required if the test is willing to
fetch the Langfuse trace, but the response-field approach is simpler
and self-contained.)

## Notes / open questions

- **Session strategy.** Cleanest is a narrow test-only admin endpoint
  on the agent that takes a shared-secret header and returns a session
  bound to a fixed practitioner uuid. Alternative: cookie capture from
  a manual browser login, pasted into env. Decide before
  implementation.
- **Upload patient.** The deployed instance has the demo cohort seeded;
  reuse one of those patients. Don't create new patients from the
  test.
- **Cache-hit observability.** If we don't want to add a response
  field, the test can fetch the Langfuse trace by `trace_id` (returned
  in the `/chat` response today) and assert the `extract_document`
  span attribute. Either is fine — pick the one that doesn't bloat
  the production response shape.
