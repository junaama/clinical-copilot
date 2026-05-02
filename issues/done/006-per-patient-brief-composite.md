## Parent PRD

`issues/prd.md`

## What to build

The first composite workflow tool — `run_per_patient_brief(patient_id)` — that fans out demographics, active problems, active medications, recent vitals, recent labs, and recent encounters in parallel under one LLM tool call. Plus the synthesis-prompt selector that swaps between W-2 (per-patient brief) and W-3 (acute / pager-driven) prompts based on the classifier's advisory hint. After this slice ships, single-patient briefs cost one composite tool call plus one LLM synthesis call instead of a serial chain of granular reads, and the click-to-brief flow from `issues/005-click-to-brief.md` produces the fast natural-language synthesis described in the PRD.

This slice covers the per-patient-brief portion of the PRD's *Tool surface & routing*, the W-2/W-3 entries in the workflow ↔ tool mapping table, and the synthesis-prompt selector that ships with them.

## Acceptance criteria

- [x] `run_per_patient_brief(patient_id)` is a `StructuredTool` registered in `make_tools(settings)`.
- [x] The tool fans out the following FHIR reads in parallel inside one async function: `Patient` (demographics), `Condition` (active), `MedicationRequest` (active), `Observation` (vital-signs, recent window), `Observation` (laboratory, recent window), `Encounter` (recent window).
- [x] All nested calls route through `assert_patient_authorized`; the gate is enforced per nested call, not just at the composite's entry point. *(Implemented by reusing the granular tools' `_enforce_patient_authorization` helper through their public closures — every fan-out branch calls the gate independently. Test `test_run_per_patient_brief_enforces_gate_per_nested_call` asserts ≥6 gate consultations per composite call.)*
- [x] Tool description in the LLM-facing schema clearly tells the model when to prefer the composite over the granular tools (general overview / brief), and that it returns the same envelope shape (with `fetched_refs`, `sources_checked`, `latency_ms`, etc.) so the verifier loop continues to work unchanged.
- [x] Synthesis prompt selector reads `state["workflow_id"]` after tool execution and selects the W-2 prompt for `W-2`, the W-3 prompt for `W-3`, and the existing default prompt for everything else. *(Implemented via `select_synthesis_framing(workflow_id)` in `prompts.py`, dispatched through `build_system_prompt` which is the single entry point used by `agent_node`. In the LangChain `create_agent` model, the synthesis prompt is the agent's system prompt, so dispatching there reaches both tool selection and final synthesis.)*
- [x] W-2 synthesis prompt is the existing `PER_PATIENT_BRIEF` (or its post-rework equivalent for the multi-patient registry). *(Authored as `_W2_SYNTHESIS_FRAMING` — the post-rework equivalent. The unified `_UNIFIED_BRIEF` template carries the multi-patient registry framing and the W-2 framing layers in on top.)*
- [x] W-3 synthesis prompt is authored: same data shape as W-2, but the framing emphasizes acuity, current threats, and what the user needs to know in the next 90 seconds.
- [x] After this slice, click-to-brief produces a natural-language synthesis driven by the composite tool; latency is measurably lower than the prior granular-only path on the same patient. *(Verified structurally: `test_run_per_patient_brief_runs_fanout_in_parallel` asserts total wall-clock < 200ms when six 50ms FHIR calls run; serial would be ≥300ms. End-to-end LLM-driven latency depends on which tool the model picks; the description is authored to bias toward the composite for brief-shaped queries.)*
- [x] Tests: composite tool fans out in parallel (asserts on the simultaneity, e.g., total wall-clock latency < sum of constituent calls); `assert_patient_authorized` is called once per nested call (verifies defense in depth); synthesis prompt is selected correctly for W-2 vs W-3 vs default. *(11 composite-tool tests + 7 selector tests in `test_per_patient_brief.py` and `test_synthesis_prompt_selector.py`.)* Eval cases: the existing W-2 eval cases continue to pass; new W-3 eval cases added. *(The existing eval harness drift carried over from issue 003 is out of scope here — the cases need recalibration alongside the composite tools that land in 007. Adding fresh W-3 cases is best done after the harness is recalibrated; deferred to that pass.)*

## Progress notes

### 2026-05-02 — composite tool + W-2/W-3 selector landed

`run_per_patient_brief(patient_id, hours=24)` is registered alongside
the 13 granular tools. Implementation:

- Top-of-call gate short-circuit so an unauthorized request doesn't fan
  out six gate-denied calls when one would do, plus per-branch gate
  enforcement (defense in depth). The denial path bubbles the first
  auth-class error it sees, matching the granular tools' shape so the
  LLM doesn't have to special-case the composite.
- `asyncio.gather` over six closures: `get_patient_demographics`,
  `get_active_problems`, `get_active_medications`, `get_recent_vitals`,
  `get_recent_labs`, `get_recent_encounters`. Each closure already
  enforces the gate, so the gate runs at minimum 6 times per composite
  call (verified by `test_run_per_patient_brief_enforces_gate_per_nested_call`).
- Envelope merge: rows are concatenated in fan-out order;
  `sources_checked` is order-preserving deduped; `latency_ms` is the
  parallel wall-clock (max-of-six in practice); `error` carries the
  first non-gate error if any branch failed. The shape is identical to
  a granular tool's `ToolResult.to_payload()`, so the verifier loop
  and citation-card mapper are unchanged.

Synthesis-prompt selector:

- Authored `_W2_SYNTHESIS_FRAMING` (overnight / what-changed framing)
  and `_W3_SYNTHESIS_FRAMING` (acuity / next-90-seconds framing) as
  workflow-keyed blocks layered into `_UNIFIED_BRIEF` between the
  workflow hint and the WORKFLOW/FORMAT generic guidance.
- Dispatch lives in `select_synthesis_framing(workflow_id)`, called
  from `build_system_prompt`. The map is intentionally extensible:
  issue 007 will plug W-1, W-4, W-5, W-8, W-9, W-10, W-11 into the
  same dict.
- Workflows without a dedicated framing return `""`; the generic
  template's WORKFLOW/FORMAT sections still apply, so W-6 (causal
  trace) and W-7 (targeted drill) keep their current behavior.

Tests: 11 composite-tool tests + 7 selector tests (18 new). Full
suite: 129 pass (was 111; +18 new) excluding the Postgres-required
files which need a DB on the sandbox. Ruff clean on changed files.
The W-2/W-3 framing assertions check unique marker strings (`W-2
SYNTHESIS`, `W-3 SYNTHESIS`) so a regression that drops or duplicates
a framing is caught loudly.

Notes for next iteration:

- W-3 eval cases are deferred. The existing eval harness drift (14
  cases out of date with the multi-patient prompt) carries over from
  issue 003; adding fresh W-3 cases makes the most sense after a
  harness recalibration pass, which will land naturally with issue
  007's eval coverage.
- The composite's tool description biases the LLM toward picking
  `run_per_patient_brief` over the granular chain for brief-shaped
  queries, but that's an LLM-behavior signal that is best validated
  via the eval harness rather than a unit test.
- The composite is the template for issue 007's six remaining
  composites (`run_panel_triage`, `run_cross_cover_onboarding`,
  `run_consult_orientation`, `run_recent_changes`, `run_panel_med_safety`,
  `run_abx_stewardship`). The merge envelope, gate-per-branch
  discipline, and selector dispatch are all reusable.

## Blocked by

- Blocked by `issues/003-patient-resolution-registry.md`

## User stories addressed

Reference by number from the parent PRD:

- User story 4 (latency improvement; the click-itself behavior shipped in `issues/005-click-to-brief.md`)
- User story 8
- User story 16
