# Week 2 Submission Readiness Checklist

The single verification document for the W2 multimodal evidence agent
submission. Maps every acceptance criterion in
`issues/w2-submission-pass-prd.md` and the parent assignment
(`AgentForgeWk2.md`) to a concrete artifact a grader (or operator
preparing the submission) can run, click, or read.

This is a **bundle**, not a new test tier. Every artifact referenced
below already exists in the repo. The point of this doc is that a
reviewer should not have to know where each piece lives — running the
checklist top to bottom (Local §3 → Deployed §4) covers visual source
grounding, cited document summary, guideline citations, supervisor
trace inspectability, the 50-case eval gate, the graph integration
layer, deployed cost / latency evidence, and the explicit caveats.

Companion documents:

| Doc | Owns |
| --- | --- |
| `agentforge-docs/SUBMISSION-SCRIPT.md` | The 5-minute demo video script. |
| `agentforge-docs/W2-COST-LATENCY-REPORT.md` | Projected cost & latency math, rate table, deploy steps. |
| `runbook/002-deployed-langfuse-measurement.md` | Manual measurement procedure that backs the cost report's "actual numbers" section. |
| `agent/tests/test_w2_reliability_live_smoke.py` | Automated `live_http` smoke for the four representative flows. |
| `agent/tests/test_http_e2e_deployed.py` | Automated `live_http` smoke for the upload-id + cache-first chain. |
| `agent/tests/test_w2_gate.py` | The pinned 50-case W2 eval gate. |
| `agent/tests/test_graph_integration.py` | The graph-layer regression tier. |

---

## 1. TL;DR

For a fast confidence pass before refreshing the submission:

```bash
# Local — deterministic regression tier (≈ 30 s, no API keys)
cd agent
.venv/bin/python3.12 -m pytest -q \
  tests/test_w2_gate.py \
  tests/test_w2_gate_regression.py \
  tests/test_graph_integration.py
cd ../copilot-ui && npx vitest run
```

```bash
# Deployed — automated live_http smoke (≈ $0.25, 60-180 s wall-clock)
cd agent
COPILOT_SESSION_COOKIE=<value-from-browser> \
  .venv/bin/python3.12 -m pytest -m live_http -v \
    tests/test_w2_reliability_live_smoke.py \
    tests/test_http_e2e_deployed.py
```

Then walk the deployed-flow steps in §4 (lab upload → intake upload →
guideline question → W1 brief regression) against the live demo and
inspect the four resulting Langfuse traces with the
field checklist in `runbook/002-deployed-langfuse-measurement.md` §5.

If every box in §3 and §4 ticks, the submission is verified.

---

## 2. Artifact map

The eight acceptance criteria for this bundle (issue 038) and where
each one is verified:

| # | Acceptance criterion | Verified by | How to run |
| --- | --- | --- | --- |
| AC1 | Lab PDF upload demonstrates extraction + visual source highlight in the deployed app. | Deployed flow §4.1 + `copilot-ui/src/__tests__/ExtractionResultsPanel.test.tsx` ("backend-shaped … rendering" + tab/source CTA cases). | §3.4 (UI vitest) + §4.1 (manual deployed click-through). |
| AC2 | Intake form upload demonstrates structured extraction + source highlight for at least one important field. | Deployed flow §4.2 + the same vitest suite (`backend-shaped intake rendering (issue 034)` describe block) + automated `live_http` case `test_smoke_intake_upload_then_chat_cites_same_document`. | §3.3 (live_http smoke) + §3.4 (UI vitest) + §4.2 (manual deployed click-through). |
| AC3 | Post-upload chat answer cites the uploaded `DocumentReference`. | `agent/tests/test_http_e2e_deployed.py` (whole file) + `agent/tests/test_w2_reliability_live_smoke.py::test_smoke_intake_upload_then_chat_cites_same_document` + deployed flow §4.2 trace inspection. | §3.3 (live_http smoke). |
| AC4 | Guideline question answer cites retrieved guideline chunks. | `agent/tests/test_w2_reliability_live_smoke.py::test_smoke_ada_a1c_question_returns_guideline_citation` + `…_kdigo_ace_arb_…` + the W2 gate's `evidence_retrieval` and `citation_contract` categories (8 + 6 cases). | §3.1 (W2 gate) + §3.3 (live_http smoke) + §4.3 (deployed flow). |
| AC5 | Langfuse trace shows supervisor routing and worker handoffs for a document or evidence turn. | Deployed inspection per `runbook/002-deployed-langfuse-measurement.md` §5 ("Supervisor handoffs" row of the trace-field checklist) + the `agent_audit` row's `extra.handoff_events` field, asserted under `agent/tests/test_supervisor_*` suites. | §3.2 (graph integration) + §4.4 (Langfuse UI walk). |
| AC6 | W2 50-case eval gate passes. | `agent/tests/test_w2_gate.py` (5 tests, pinned 50-case fixture set, distribution-locked). | §3.1. |
| AC7 | Graph integration test layer passes. | `agent/tests/test_graph_integration.py` (the ainvoke-the-real-graph tier). | §3.2. |
| AC8 | Deployed measurement evidence is available for latency, token, model, and cost reporting. | `runbook/002-deployed-langfuse-measurement.md` §3 + §5 + §8 + `agentforge-docs/W2-COST-LATENCY-REPORT.md`. | §4.4 (manual smoke + Langfuse trace inspection). |
| AC9 | Submission caveats are explicit and do not contradict the parent PRD. | §5 of this document. | §5. |

---

## 3. Local verification (deterministic, no API keys)

These steps run on the host without any deployed infrastructure. They
are the regression tier — if any of them fails on `main`, the
submission story is broken before the deployed pass even matters.

### 3.1 W2 50-case eval gate (AC6, partial AC4)

```bash
cd agent
.venv/bin/python3.12 -m pytest -q tests/test_w2_gate.py tests/test_w2_gate_regression.py
```

**Expected:** all five tests pass. The gate enforces:

* exactly 50 fixture cases under `agent/evals/w2/` (PRD-pinned count).
* the eight categories distribute as `lab_extraction:10, intake_extraction:8, evidence_retrieval:8, supervisor_routing:6, citation_contract:6, safe_refusal:6, no_phi_in_logs:3, regression_w1:3` — drift trips `test_case_count_distribution_matches_prd`.
* every case meets its declared `expected` rubric verdict.
* per-rubric pass rate clears the `GATE_THRESHOLDS_W2` floor.
* no rubric drops more than 5 pp against the committed `.eval_baseline.json`.

If the gate fails, **do not refresh the submission** — fix the
regression first. The grader's "introduce a regression and confirm CI
fails" check fires against this same gate.

CLI alternative (used by the pre-push hook): `cd agent && uv run python
-m copilot.eval.w2_baseline_cli check`. Same result, prettier output.

### 3.2 Graph integration tier (AC7, AC5)

```bash
cd agent
.venv/bin/python3.12 -m pytest -q tests/test_graph_integration.py
```

**Expected:** all cases pass. This tier exercises `build_graph(...).ainvoke(...)`
with the real LangGraph wiring (only the chat model and document
upload boundaries are stubbed) and pins the four 2026-05-06 production
bugs as regression cases — see the issue brief at
`issues/done/021-graph-integration-test-layer.md`. Supervisor routing,
handoff events, and the verifier loop are all observable here without
opening a browser.

### 3.3 Deployed `live_http` smoke (AC2, AC3, AC4)

```bash
cd agent
COPILOT_SESSION_COOKIE=<value-from-browser> \
  .venv/bin/python3.12 -m pytest -m live_http -v \
    tests/test_w2_reliability_live_smoke.py \
    tests/test_http_e2e_deployed.py
```

The cookie is captured from a manual login as `dr_smith` at
`https://copilot-agent-production-3776.up.railway.app`. With no cookie
set the cases all SKIP cleanly (verified by both files' `pytestmark`).

**Expected:** all cases pass. Behavior pinned:

* upload type-mismatch → HTTP 409 `doc_type_mismatch` (issue 024).
* intake upload → 200 with populated `intake` payload + `discussable=True` (issue 025).
* post-upload chat turn → cites the same `DocumentReference/<id>` (issue 026).
* upload returns a real id, not the synthetic `openemr-upload-` prefix (issue 022).
* second chat turn against the same document → non-empty `state.cache_hits` (issue 023).
* ADA A1c and KDIGO ACE/ARB chat blocks → carry at least one `card == "guideline"` citation (issues 027 + 028).

Cost: ≈ $0.25 per full run (one VLM upload + four chat turns).
Wall-clock 60-180 s, dominated by the VLM call and one cold-start
synthesizer turn.

### 3.4 Frontend test suite (AC1, AC2)

```bash
cd copilot-ui
npx vitest run
```

**Expected:** all cases pass (most recent commit: 136 / 136). The
suites that anchor source-grounding behavior:

* `ExtractionResultsPanel.test.tsx` — tab control, source CTA gating
  on exact bbox-path match, Source-tab selection handoff, normalized
  bbox overlay positioning, no-CTA invariant when bboxes are empty.
* `FileUploadWidget.test.tsx` — onUploaded contract carries the
  original `File` so the panel can render the preview without a new
  authenticated download endpoint.
* `citations.test.ts` — citation chip routing for `card == "guideline"`
  vs. chart-card postMessage path.

`tsc --noEmit` is also expected to be clean. ESLint currently fails
with a pre-existing root-config `Cannot find package 'globals'` error
unrelated to this submission — flagged in `issues/done/032-…` notes.

---

## 4. Deployed verification (manual click-path on Railway)

Sign in to the live demo at
`https://copilot-agent-production-3776.up.railway.app/` as `dr_smith`.
Open Co-Pilot. Select patient `p01 — Wei Chen` (the first row of the
care-team panel). Run the four flows below, in order. Wait for each
block to render fully before the next one — Langfuse exports a
separate trace per `/chat` and per `/upload` call, and rapid-fire
prompts produce harder-to-correlate traces.

The full procedure (demo account password recovery, patient FHIR uuid
reseeding, fixture document paths, Langfuse trace-field inspection,
PHI safety check) is documented in
`runbook/002-deployed-langfuse-measurement.md`. The four-flow summary
below is the checklist surface; consult the runbook for the operating
detail behind each step.

### 4.1 Lab PDF upload (AC1, AC8)

* Upload `example-documents/lab-results/p01-chen-lipid-panel.pdf` with document type `Lab PDF`.
* **Verify Results tab:** structured lipid-panel rows (LDL, HDL, total cholesterol, triglycerides) render. Source CTAs appear on at least the abnormal-flag rows whose `field_path` matched a bbox.
* **Verify Source tab:** clicking any CTA switches tabs, the rendered PDF page paints with all bboxes faintly drawn, the selected bbox is prominent.
* **Verify chat synthesis:** the synthetic post-upload turn cites the `DocumentReference/<id>` returned by the upload, with extracted lab values framed as "the uploaded document records …" rather than as chart entries (per issue 035 hardening).

### 4.2 Intake form upload (AC2, AC3)

* Upload `example-documents/intake-forms/p01-chen-intake-typed.pdf` with document type `Intake form`.
* **Verify Results tab:** chief concern, demographics (name / DOB / gender / phone / address / emergency contact), current medications, allergies, family history, and social history all render. Source CTAs appear on the high-priority intake fields named in the PRD: `chief_concern`, `demographics.name`, `demographics.dob`, `current_medications[i].name`, `allergies[i].substance`, `family_history[i].condition`.
* **Verify backend-shape alignment:** demographics keys are `dob` / `gender` (not `date_of_birth` / `sex`); social history keys are `smoking` / `alcohol` / `drugs` / `occupation`; intake medication / allergy / family-history rows have **no** confidence badge — those models do not carry one (issue 034).
* **Verify chat synthesis:** the post-upload turn cites the `DocumentReference/<id>` and frames extracted intake facts as document-sourced, not chart-sourced.

### 4.3 Guideline question (AC4)

* Send the prompt: *What does the ADA recommend for A1c targets in adults with type 2 diabetes?*
* **Verify reply block:** carries at least one citation with `card == "guideline"` (or `fhir_ref` starting `guideline:`) per the issue-027 wire contract. The text references the indexed corpus chunk, not general model knowledge.
* **Verify fail-closed gate:** running the same flow against an out-of-corpus question (e.g. *What does the WHO recommend for sepsis fluid resuscitation in pediatric patients?*) should produce an explicit no-corpus refusal, not a fabricated answer (issue 036).

### 4.4 Langfuse trace inspection (AC5, AC8)

Open `https://langfuse-web-production-b665.up.railway.app`. Project
`copilot`, environment `default`. Filter by today's date and trace
name `LangGraph`. The four most recent traces correspond to §4.1 –
§4.3 plus a W1 brief regression — confirm by clicking each and
matching the input prompt text.

For each trace, walk the per-trace checklist in
`runbook/002-deployed-langfuse-measurement.md` §5 and confirm the
eight observability fields are present: token counts, model names,
tool sequence, supervisor handoffs, latency spans, cost estimates,
retrieval hits (W-EVD only), and extraction confidence (`/upload`-side
trace only). The intake-upload trace must show the
`supervisor → intake_extractor` (CHAIN) → `ChatOpenAI` (GENERATION) →
`verifier` (CHAIN) handoff structure — that's the AC5 evidence.

Run the §6 PHI safety check on the same traces before publishing
screenshots: free user / assistant text restricted to synthetic Chen
strings, supervisor reasoning carries no name / DOB / MRN, tool-result
prompt context strips identifiers, document body is structured-JSON
output only, audit-log row carries `final_response_chars` and not
`final_response`.

### 4.5 W1 brief regression (sanity check)

* Click `p01 — Wei Chen` in the panel to fire the synthetic per-patient brief.
* **Verify reply:** vitals / problems / medications / encounters citation chips render; clicking a chip flashes the corresponding chart card (the `copilot:flash-card` postMessage).
* **Verify trace shape:** the brief trace is rooted at `LangGraph` with `classifier → ChatOpenAI` (workflow_id `W-2`) → parallel FHIR tool calls → synthesis → verifier. There must be **no** `supervisor` span on the brief turn — its presence is a routing regression, not a feature (called out in `runbook/002` §3.4).

---

## 5. Submission caveats (AC9)

Caveats stay narrow and are explicitly drawn from the PRD's "Out of
Scope" section and the design decisions in §147-206. They are listed
here so the submission story does not contradict itself in a side
channel.

| Caveat | Why this is intentional | PRD anchor |
| --- | --- | --- |
| Extracted lab values are not promoted into first-class OpenEMR lab Observations. They persist as document-linked annotations only. | The available OpenEMR write surface does not provide a reliable lab Observation creation path, and overclaiming chart-write would contradict the corpus-bound and decision-support framings. | PRD §49–52, §182–183, "Out of Scope" §250–251, issue 035 hardening. |
| The browser-local PDF/image source viewer does not yet support search, thumbnails, zoom persistence, annotation editing, or download. | The submission-pass UI optimizes for the visual source contract (page-aware bbox overlay), not for a full document reader. A new authenticated document-download endpoint was avoided so the submission stays narrow. | PRD §136–138, §172–179, "Out of Scope" §245–248. |
| The agent does not place orders, prescribe, start / stop / titrate doses, or perform any autonomous chart writes. It produces evidence-grounded considerations. | The decision-support stance is required by the W-DOC and W-EVD prompts (issues 035 + 036). Autonomous-action requests are refused at the synthesizer layer before they reach a worker. | PRD §54–57, §189–193, "Out of Scope" §253–254. |
| Guideline retrieval is corpus-bound. Out-of-corpus questions get an explicit refusal, not an answer from general model knowledge. | The submission's RAG behavior is an indexed-corpus claim, not a "ask anything clinical" claim. The fail-closed gate (issue 028) ensures uncited answers never reach the user; the corpus-bound prompt (issue 036) ensures the synthesizer doesn't fall back on memory. | PRD §54–55, §187–190, "Out of Scope" §256. |
| The supervisor exists for inspectable routing and narrow worker tool ownership. There is no critic agent and no autonomous multi-agent clinical reasoning beyond the supervisor → worker → verifier chain. | A wider multi-agent surface would expand the threat model without adding clinical value at the submission scope. | PRD §194–196, "Out of Scope" §257. |
| New document types beyond lab PDF and intake form, multi-vendor VLM failover, and corpus expansion beyond the demo set are out of scope. | The submission's multimodal claim is bounded by the two document types the eval gate fixtures cover. | PRD "Out of Scope" §252, §255, §257. |
| Demo video recording, narration, and final report prose are tracked separately from this checklist. | The PRD scopes those as operational tasks. The script lives at `agentforge-docs/SUBMISSION-SCRIPT.md`. | PRD "Out of Scope" §259–261. |

### Known follow-ups (not blockers)

These are documented gaps, all observability ergonomics rather than
behavior regressions:

* Trace-level `totalCost` is `None` in the deployed Langfuse export.
  The cost report sums per-generation cost client-side. Tracked in
  `runbook/002-deployed-langfuse-measurement.md` §8.
* `retrieve_evidence` TOOL span has `input` / `output` set to `null`
  in the saved trace fixture — populated correctly in the live UI;
  fixture-export quirk only.
* Extraction-confidence values surface only in `/upload`-side traces,
  not in `/chat`-side traces. The §4.4 walk inspects both kinds.

---

## 6. What this checklist is *not*

* It is not the W2 cost-and-latency report (`agentforge-docs/W2-COST-LATENCY-REPORT.md`).
* It is not the demo video script (`agentforge-docs/SUBMISSION-SCRIPT.md`).
* It is not the deployed Langfuse measurement runbook (`runbook/002-deployed-langfuse-measurement.md`) — that is the operational measurement procedure; this checklist references it.
* It is not a replacement for the W2 eval gate or the graph integration tier — those are the regression proof; this checklist is the verification map that says where to find them.

When the submission needs a single "is everything ready?" inspection,
this is that document. Everything else is downstream.
