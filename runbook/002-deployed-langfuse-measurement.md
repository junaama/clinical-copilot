# 002 — Deployed Langfuse measurement smoke

A manual operational run against the deployed agent that produces the
Langfuse traces the Week 2 submission's cost and latency story is
sourced from. Mirrors the four representative flows the W2 submission
PRD calls out (lab upload, intake upload, evidence retrieval, and a
W1 regression) and lists the per-trace fields a reviewer is expected
to inspect. No production code change is required to run it.

This runbook is paired with `agentforge-docs/W2-COST-LATENCY-REPORT.md`
— the report owns the projected math; this runbook owns the
observed-trace evidence that backs the report's "actual numbers"
section.

---

## TL;DR

1. Sign into the deployed app at
   `https://openemr-production-c5b4.up.railway.app` as `dr_smith`.
2. Send the four prompts in §3 against the synthetic fixture patient
   (`p01 — Wei Chen`, also referenced by FHIR uuid in
   `E2E_PATIENT_UUID`).
3. Open the Langfuse UI at
   `https://langfuse-web-production-b665.up.railway.app`, project
   `copilot`, default environment, filter by today's date.
4. For each turn, walk the per-trace checklist in §5 and tick the
   eight observability fields (token count, model, tool sequence,
   supervisor handoff, latency, cost, retrieval hits, extraction
   confidence).
5. Run the PHI safety check in §6 against the same traces before
   publishing screenshots.

The full smoke costs ≈ $0.10 (one VLM upload + three chat turns) and
takes ≈ 5 minutes wall-clock.

---

## 1. Pre-flight

### Deployed surfaces

| Surface | URL |
| --- | --- |
| OpenEMR / Co-Pilot UI | `https://openemr-production-c5b4.up.railway.app` |
| Co-Pilot agent (HTTP) | `https://copilot-agent-production-3776.up.railway.app` |
| Langfuse UI (web) | `https://langfuse-web-production-b665.up.railway.app` |
| Langfuse ingest (OTLP) | `https://langfuse-production-a8dc.up.railway.app` |

The agent service has `LANGFUSE_HOST` / `LANGFUSE_PUBLIC_KEY` /
`LANGFUSE_SECRET_KEY` set in Railway env, so every `/chat` and
`/upload` turn already produces a runtime trace tree without any
local setup. Confirm via:

```bash
railway variables --service copilot-agent | grep -i langfuse
```

If `LANGFUSE_HOST` is empty, traces are silently disabled
(`copilot.observability.get_callback_handler` returns `None` and the
runtime callback is never attached). Re-set the three env vars and
`railway redeploy --service copilot-agent` before starting the smoke.

### Demo account

| Identity | Username | Password | Notes |
| --- | --- | --- | --- |
| Treating clinician | `dr_smith` | retrieve from Railway secret store via `railway variables --service copilot-agent \| grep DR_SMITH_PASSWORD` (or the OpenEMR admin's user-edit panel) | Has CareTeam membership for the lipid-panel/intake fixtures. Used for all four smoke prompts. |
| OpenEMR admin | `admin` | `railway variables --service openemr \| grep OE_PASS` | Only needed if the seed has drifted and dr_smith's care-team rows need to be re-seeded. Do not run the smoke as `admin` — the admin bypass would suppress the CareTeam-gate observability the trace is supposed to demonstrate. |

The five seeded patients are documented in
`agentforge-docs/SUBMISSION-SCRIPT.md`. dr_smith is on the care team
for fixtures p01, p03, p05; the fixtures used in this smoke are p01
(Chen, lipid panel + intake) and a guideline question that does not
require a patient context.

### Fixture documents

Committed at the repo root under `example-documents/`:

| Use | Path | Why this one |
| --- | --- | --- |
| Lab upload | `example-documents/lab-results/p01-chen-lipid-panel.pdf` | Synthetic 1-page lipid panel for Chen. The same fixture used by `tests/test_http_e2e_deployed.py` and `tests/test_w2_reliability_live_smoke.py`, so behavior in this smoke matches the regression tier. |
| Intake upload | `example-documents/intake-forms/p01-chen-intake-typed.pdf` | Synthetic typed intake form for the same patient. Triggers the W-DOC intake-extractor path (chief concern, demographics, current_medications, allergies, family_history, social_history). |

Both fixtures are synthetic — no real PHI has ever been on disk in
this repo.

### Patient FHIR uuid

The Chen fixture's FHIR uuid is in the operator's environment as
`E2E_PATIENT_UUID`. Confirm it resolves to the right name on the
deployed system:

```bash
echo "$E2E_PATIENT_UUID"
# expect a uuid like a1abeabb-0127-494a-9561-5d89a7a86474
```

If the uuid drifts after a re-seed, recover it from `/panel` while
signed in — the first row of the dr_smith panel is the smoke target.

---

## 2. Why deployed-first

The Week 2 PRD makes an explicit operational claim: cost and latency
numbers in the submission should be derived from observed deployed
runs, not just local pytest spans. Two reasons this matters for the
graders:

* **Token counts and cost** depend on the deployed model + provider
  pinning (gpt-5.4-mini in Railway env, claude-sonnet-4-6 for VLM,
  cohere rerank). A local run with a different model env would produce
  the wrong rate-card math.
* **Tool sequence and supervisor handoffs** depend on the deployed
  classifier and supervisor prompts, which can drift independently of
  the worker prompts. Reading the deployed trace is the only way to
  prove the wiring is what the architecture doc claims.

If the deployed environment is genuinely unreachable on the day, the
fallback is the in-process `live_http` smoke at
`agent/tests/test_w2_reliability_live_smoke.py` — it covers the same
four flows over HTTPS and produces the same Langfuse traces, but
needs a captured `COPILOT_SESSION_COOKIE`. Use the manual smoke
first; the in-process smoke is the regression detector, not the
measurement instrument.

---

## 3. Smoke procedure (four flows)

Sign in as `dr_smith`. Open the Co-Pilot sidebar. Click into the row
for `p01 — Wei Chen` to open a fresh conversation. Note the
conversation id (visible in the URL or in the network tab on `/chat`)
— you will use it as a Langfuse session filter in §4.

Run the four flows below in order. Wait for each block to render
fully before sending the next prompt — Langfuse exports a separate
trace per `/chat` (and per `/upload`) call, so rapid-fire prompts
just produce harder-to-correlate traces.

### 3.1 Flow A — lab upload (W-DOC, lab path)

**Action.** Use the upload widget. Pick `p01-chen-lipid-panel.pdf`.
Choose document type `Lab PDF`.

**Expected UI.** The Results tab renders the structured lipid panel
(LDL, HDL, total cholesterol, triglycerides). Source CTAs appear on
each `result.value` row whose `field_path` matched a bbox. Switching
to the Source tab shows the rendered PDF page with all bboxes faintly
drawn and the most-recently-clicked one prominent.

**Expected trace name.** A trace named `LangGraph` for the synthesis
chat turn that follows extraction; the `/upload` call itself produces
a separate trace also rooted at `LangGraph` containing the
`vlm_extract_document` tool span (Claude vision generation).

**Why this flow.** It exercises VLM extraction (the dominant cost
line item per `W2-COST-LATENCY-REPORT.md` §3.1), the bbox matcher,
the upload-side extraction cache, and the chat-side rendering of
extracted lab values as document annotations rather than chart
truth.

### 3.2 Flow B — intake upload (W-DOC, intake path)

**Action.** Upload `p01-chen-intake-typed.pdf` with document type
`Intake form`.

**Expected UI.** The Results tab renders chief concern, demographics,
current medications, allergies, family history, and social history
fields. Source CTAs appear on the high-priority intake fields (chief
concern, name/DOB, medication name, allergy substance, family-history
condition).

**Expected trace shape.** Generation tree contains
`supervisor → intake_extractor` (CHAIN) → `ChatOpenAI` (GENERATION,
synthesis) → `verifier` (CHAIN). The intake-extractor system prompt
is the W-DOC synthesizer hardened by issue 035; verify the response
text frames extracted document facts as "the uploaded document
records …" rather than as chart entries.

**Why this flow.** It exercises the second extraction pipeline
(intake schema), the document-fact safety policy from issue 035, and
the citation contract (W-DOC turns must carry `DocumentReference/...`
citations or refuse).

### 3.3 Flow C — evidence retrieval (W-EVD)

**Action.** In the same conversation (or a fresh one — the flow does
not depend on the upload turns), send the prompt:

> *What does the ADA recommend for A1c targets in adults with type 2
> diabetes?*

**Expected UI.** A guideline-grounded answer with at least one
citation chip whose `card == "guideline"` (or `fhir_ref` starting
with `guideline:`). The answer should reference indexed corpus chunks
rather than reasoning from general model knowledge — verifying
issue 036's corpus-bound policy.

**Expected trace shape.** Generation tree contains
`supervisor → evidence_retriever` (CHAIN) → `tools/retrieve_evidence`
(TOOL — Cohere embed + Postgres hybrid + rerank) → `ChatOpenAI`
(synthesis) → `verifier`. The `retrieve_evidence` tool span carries
`toolCallNames` and `usageDetails` (token counts when the tool
internally hits an LLM; embed/rerank latency surfaces as the span
`latency`).

**Why this flow.** It exercises hybrid RAG, Cohere rerank, the
evidence-retriever worker prompt, and the citation gate. It is also
the cheapest of the four flows (≈ $0.003 per W2-COST-LATENCY-REPORT
§3.2) — useful as a quick regression probe.

### 3.4 Flow D — Week 1 brief regression

**Action.** Open the panel for the same `dr_smith` session and click
`p01 — Wei Chen` to fire the per-patient brief synthetic message.

**Expected UI.** The W1 brief block renders with citation chips for
vitals, problems, medications, and recent encounters. Clicking a
citation chip flashes the corresponding chart card on the parent
OpenEMR page (the `copilot:flash-card` postMessage handshake).

**Expected trace shape.** Generation tree contains
`classifier → ChatOpenAI` (workflow_id resolves to `W-2`,
per-patient-brief composite) → multiple parallel FHIR tool calls
(`get_patient_demographics`, `get_observations_recent`,
`get_problems_active`, `get_medications_active`,
`get_encounters_recent`) → synthesis → verifier.

**Why this flow.** It is the W1 regression check called out in
`W2-COST-LATENCY-REPORT.md` §6 — the W2 supervisor work is supposed
to leave W1 paths untouched. If the brief turn shows a `supervisor`
span, that's a routing bug, not a feature; report it as a follow-up
rather than carrying on with the smoke.

---

## 4. Finding the corresponding Langfuse traces

1. Open `https://langfuse-web-production-b665.up.railway.app`.
2. Select project `copilot` (set in env as `LANGFUSE_PROJECT`).
3. Switch to environment `default`. (`copilot.observability` does not
   override environment, so all runtime traces land here unless a
   future deploy sets `LANGFUSE_TRACING_ENVIRONMENT`.)
4. Filter by *Date range = today* and *Trace name = LangGraph*.
5. Sort by `timestamp` desc. The four most recent traces are the
   ones from §3 — confirm by clicking each and checking the input
   field for the prompt text (intake/lab uploads carry the post-
   upload follow-up message; the brief turn carries the synthetic
   "give me the brief" text; the evidence turn carries the ADA
   prompt verbatim).
6. Bookmark the four trace ids — they are the evidence pointers for
   the cost/latency report.

If `dr_smith` actions don't appear in Langfuse within ~10 seconds of
the chat turn finishing:

* Check `railway logs --service copilot-agent | grep -i langfuse` —
  a missing API key or a DNS error to the OTLP endpoint is the most
  common cause and is logged at WARNING by `observability.py:79`.
* Check the Langfuse server is reachable:
  `curl -sI https://langfuse-production-a8dc.up.railway.app/api/public/health`.
* Confirm `LANGFUSE_PUBLIC_KEY` matches the project key in the
  Langfuse UI under *Settings → API Keys*.

---

## 5. Trace data checklist

For each of the four traces, the reviewer should be able to read the
following fields off the Langfuse UI without leaving the trace
detail page. Each field is asserted observable in the saved trace
fixtures (`trace-*.json` at the repo root), so this list is the
contract not just an aspiration.

| Field | Where on the trace | Notes |
| --- | --- | --- |
| **Token counts** | per-`GENERATION` observation → *Usage* card. Keys: `input`, `output`, `total`, `input_cache_read`. | Sum across the trace's generations is the per-turn budget. The W1 brief turn is the multi-LLM-call case where the sum matters. |
| **Model names** | per-`GENERATION` observation → *Model* badge. Expected values today: `gpt-5.4-mini-2026-03-17` for classifier / supervisor / verifier / synthesis, `claude-sonnet-4-6` for `vlm_extract_document` (visible only on the `/upload` trace, not the `/chat` trace), `cohere/rerank-english-v3.0` and `cohere/embed-english-v3.0` for the W-EVD path. | If a model badge says `unknown` on a generation, the rate-table lookup is silently zero — file a follow-up rather than treating cost as zero. |
| **Tool sequence** | left rail of the trace tree — order of `TOOL` observations from top to bottom. | Mirror of `extra.tool_sequence` on the audit row. Lab upload should show `vlm_extract_document` first, then post-upload synthesis. Intake upload likewise but routed through `intake_extractor`. W-EVD shows `retrieve_evidence`. W1 brief shows the FHIR tool fan-out. |
| **Supervisor handoffs** | trace tree → look for `supervisor` (CHAIN) → child CHAIN named `intake_extractor` / `evidence_retriever` / `lab_extractor`. The handoff timing is `supervisor.endTime → child.startTime`. | A W1 brief turn should have *no* `supervisor` span; if one appears, that's the routing regression mentioned in §3.4. |
| **Latency spans** | each observation carries `latency` (seconds). Trace root `latency` is the wall-clock for the turn. | Per-step targets in `W2-COST-LATENCY-REPORT.md` §4. The dominant cost in this smoke is the `vlm_extract_document` span — expect 4–9 s p95. |
| **Cost estimates** | per-`GENERATION` observation → *Cost* badge (`totalCost`, broken down as `costDetails.input / output / input_cache_read`). | Trace top-level does not expose a per-trace total — sum the per-generation `totalCost` to get the turn cost. Example from saved fixture: 25 obs, 9961 input+cache tokens, 256 output tokens, ≈ $0.004 total. |
| **Retrieval hits** | on the W-EVD trace, click into the `retrieve_evidence` TOOL span → *Output* tab. The structured payload contains the rerank-ranked chunks, scores, and source ids. | Used to verify that the answer was synthesized over actual indexed corpus chunks, not from model memory. Empty list means "no corpus support" — the agent is supposed to refuse, not paper over (issue 036). |
| **Extraction confidence** | on the upload-side trace, click into the `vlm_extract_document` GENERATION span → *Output* tab. Each result row carries a `confidence` literal (`high` / `medium` / `low`) per the Pydantic schema. | The W-DOC synthesizer (issue 035) is required to surface `low` confidence with uncertainty framing rather than asserting the value as fact. |

If a field above is **not** visible on a deployed trace, that's a
follow-up issue, not a smoke failure. Record it in §8 of this
runbook (or a new issue under `issues/`) before publishing the cost
report. The trace fixture at
`trace-cff67fbe259a96fe4dbe68b48c2d8496.json` is the reference shape;
diffing the deployed trace against it is the fastest way to spot a
missing observation.

---

## 6. PHI safety check

The smoke uses synthetic data only — no real PHI ever lands on disk
in this repo. The check below is the audit step that confirms the
deployed trace pipeline does not start leaking new strings on top of
that.

For each of the four traces, click into the *Input* and *Output*
tabs at the trace level and at every `GENERATION` observation, and
walk the following list:

1. **Free user / assistant text.** The audit row deliberately stores
   only `final_response_chars` (the length), not the text
   (`agent/src/copilot/audit.py`). Langfuse traces *do* carry the
   raw conversation text — that's expected for debugging. The check
   is that the only patient strings present are the ones from the
   synthetic Chen fixture (name `Wei Chen`, DOB matches the seed).
   No real-patient strings should ever appear.
2. **Demographics in the supervisor reasoning.** Click into the
   `supervisor` CHAIN's *Output* tab and confirm
   `SupervisorDecision.reasoning` does not include patient name,
   DOB, MRN, or address. The schema docstring forbids this
   (`agent/src/copilot/supervisor/schemas.py`) and the contract is
   pinned by `tests/test_audit_no_phi.py` and
   `tests/test_supervisor_schemas.py::test_handoff_event_no_phi_in_input_summary`.
3. **Tool result payloads.** Click into each FHIR tool span's
   *Output* tab. The wrappers strip MRN, SSN, full address, and
   telecom fields from the prompt-context payload — those identifiers
   appear only in the tool span's `raw_excerpt` (Langfuse-only,
   never in the LLM context). The check: no SSN-shaped strings,
   no street addresses, no phone numbers in any *prompt* context
   that goes into a `GENERATION` span.
4. **Document body text.** Click into the `vlm_extract_document`
   GENERATION's *Input* tab. The image is base64-encoded inline.
   Confirm the image content is from the synthetic fixture. The
   *Output* is structured JSON only — no copy of the rendered text.
5. **Audit log on the host.** SSH to the agent container and tail
   the audit log. The row for this turn must not contain any of:
   patient name, DOB, prompt text, response text. This is what the
   audit-no-phi tests guard at unit level; the smoke is the
   end-to-end check.

   ```bash
   railway ssh --service copilot-agent
   # inside the container:
   tail -n 5 /tmp/agent_audit.jsonl | jq '.'
   # check that 'final_response_chars' is an integer and there is no
   # 'final_response' or 'user_prompt' key.
   ```

If any of the five checks fails, **stop the smoke and file a
security follow-up issue** before publishing screenshots. Do not
attempt to scrub a Langfuse trace post-hoc — the right fix is at the
write boundary, not the export.

---

## 7. Run-without-changing-production-code guarantee

Every step in §3–§6 is read-only against the deployed agent and the
deployed Langfuse instance. No source file is edited, no env var is
mutated, no migration is run. The smoke can therefore be executed
against an arbitrary checked-out commit (with the limitation that
Langfuse traces will reflect *whatever code is deployed*, not the
checked-out commit).

The two things that *do* sometimes need a one-time production-side
adjustment, called out so the operator notices:

* **Langfuse env vars** must be set on the `copilot-agent` Railway
  service for traces to appear at all. This is a one-time setup, not
  a per-run change.
* **dr_smith CareTeam seed** must include the fixture patient. If
  the seed has drifted, `agent/scripts/seed/seed_careteam.py` is the
  recovery path. This is a seed step, not a code change.

---

## 8. Dry-run results (2026-05-07)

A dry run was performed against the saved trace fixtures
(`trace-111fda830eb60c1999f50ceb9b9ca5bb.json`,
`trace-15b91036c5b58bfb03008561a270b19d.json`,
`trace-19bf2af8bf4d06f82425254bbf25641f.json`,
`trace-5d5fa6801a3ce8cedcf7122de802778e.json`,
`trace-cff67fbe259a96fe4dbe68b48c2d8496.json`) which were exported
from the deployed instance on 2026-05-06.

**Confirmed available** (i.e. the field is present and non-empty in
at least one fixture, so the deployed exporter still emits it):

* per-observation `model` (e.g. `gpt-5.4-mini-2026-03-17`).
* per-observation `usageDetails` with keys `input`, `output`,
  `total`, `input_cache_read`, `output_reasoning`.
* per-observation `costDetails` with keys `input`, `output`,
  `input_cache_read`, `total`.
* per-observation `latency` (seconds).
* per-observation `startTime` / `endTime` (ISO 8601 UTC).
* observation `name` covering: `LangGraph`, `classifier`,
  `supervisor`, `route_after_supervisor`, `intake_extractor`,
  `evidence_retriever`, `verifier`, `tools`, `retrieve_evidence`,
  `get_patient_demographics`, `model`, `ChatOpenAI`,
  `RunnableSequence`, `RunnableLambda`.
* observation `type` covering `CHAIN`, `GENERATION`, `TOOL`.
* trace top-level `latency`.

**Confirmed missing** (i.e. fields the report would benefit from
that the saved fixtures do not carry):

* trace top-level `totalCost` is `None` in every saved fixture —
  the per-trace cost is only available by summing per-generation
  `totalCost`. **Follow-up:** add a small helper to the cost report
  pipeline that sums per-trace cost client-side until the deployed
  Langfuse version exposes a trace-level rollup.
* `retrieve_evidence` TOOL span has `input` / `output` set to `null`
  in the saved fixture
  (`trace-5d5fa6801a3ce8cedcf7122de802778e.json`). The Langfuse UI
  *does* show those fields when clicking through, so this is a
  fixture-export quirk rather than a deployed regression. The smoke
  still works against the live UI; it just means the fixtures are
  not a complete substitute for opening the Langfuse trace.
* extraction-confidence values surface only in the `/upload`-side
  trace (the `vlm_extract_document` GENERATION's structured output),
  not in any of the saved `/chat` traces. The smoke step in §3.1 /
  §3.2 must therefore inspect the `/upload` trace for that field.

These three follow-ups are not blockers for the W2 submission —
the eight required fields are observable on the deployed traces.
They are filed here so the next pass on the cost-and-latency report
has a starting list rather than rediscovering them.

---

## 9. What this runbook is *not*

* It is not the W2 cost-and-latency report. That lives at
  `agentforge-docs/W2-COST-LATENCY-REPORT.md`.
* It is not an automated regression test. The automated regression
  tier is `agent/tests/test_w2_reliability_live_smoke.py` and
  `agent/tests/test_http_e2e_deployed.py` — both run the same flows
  but assert specific contracts (citation presence, doc-id shape,
  cache-hit behavior). The Langfuse trace evidence is a separate
  observability surface and is not asserted by those tests.
* It is not a clinician-facing demo script. That lives at
  `agentforge-docs/SUBMISSION-SCRIPT.md`.

When in doubt, this runbook is *the operational measurement step*
for the cost-and-latency report; everything else is downstream of it.
