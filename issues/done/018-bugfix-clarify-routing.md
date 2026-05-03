## Parent PRD

`issues/eval-suite-v2-prd.md`

## What to build

Independent bug-fix slice. Investigate and fix the systemic clarify-routing bug surfaced by the eval suite: the agent responds with "Please provide the patient's name" on single-patient questions even when `patient_id` is already bound in the request context. This affects multiple smoke and golden cases (e.g. `smoke-003-overnight-event`, `smoke-005-imaging-result`, `golden-w2-001-eduardo-overnight`).

Likely location: classifier prompt or routing edge in `agent/src/copilot/graph.py`. The fix should ensure the classifier routes single-patient questions with a bound `patient_id` straight into the `agent` path rather than the `clarify` path.

Re-run the existing 22-case suite after the fix and document the actual pass-rate lift in the run output. The lift is reported honestly whether it matches the hypothesised 23% → 70–85% range or not.

This slice runs in parallel to the eval-scaffolding stream — it touches `agent/src/copilot/graph.py` only and does not conflict with any eval-package work.

See PRD "Problem Statement" (the two systemic bugs), "Solution" (parallel bug-fix stream), and User Story 22.

## Acceptance criteria

- [x] Root cause identified and documented in commit message: which prompt / edge / branch routes the single-patient case into clarify
- [x] Fix lands in `agent/src/copilot/graph.py` (or wherever the routing decision lives)
- [x] Existing 22-case suite re-run after fix; pass rate documented before and after
- [x] Cases that previously failed on "Please provide the patient's name" now pass or fail on substantive grounds (not on the missing-patient-id response)
- [x] No regression on `smoke-001-basic-brief` (the one currently passing case) or any other case
- [x] Fix does not introduce new failures in cases that were previously passing

## Progress notes

**Root cause** — two layers of the same bug:

1. **Classifier routing.** `classifier_node` in `agent/src/copilot/graph.py`
   sees only the latest user message — not whether `patient_id` (from
   session_context) or `focus_pid` (resolved earlier) is bound in state.
   For a question like "What happened to this patient overnight?" with no
   patient name in the text, the classifier reasonably emits `unclear` /
   low-confidence. The prior routing rule (`workflow_id == "unclear" or
   confidence < 0.8 → clarify`) then fired even though the agent already
   had patient context and could have answered. The classifier exception
   path also failed open to clarify, ignoring the bound-patient case.
2. **Agent system prompt.** Even when routing landed on `agent_node`, the
   PATIENT RESOLUTION block told the LLM "When the user mentions a
   patient by name, call resolve_patient FIRST … `clarify` — input too
   sparse. Ask for the patient's name." With no name in the question,
   gpt-4o-mini interpreted that as license to ask the user for a name
   instead of using the focus pid sitting in the registry block.

**Fix.**

- Extracted the post-classifier routing decision into a pure helper
  `_route_after_classifier(workflow_id, confidence, patient_id,
  focus_pid)` in `graph.py`. Whenever a non-empty `patient_id` or
  `focus_pid` is bound, the helper short-circuits to `agent` regardless
  of classifier confidence. Empty-string pids (the panel-spanning UC-1 /
  UC-10 shape) are treated as unbound so cold-start clarify still fires
  when truly ambiguous. The classifier exception path uses the same
  helper.
- Strengthened the unified system prompt in
  `agent/src/copilot/prompts.py`: PATIENT RESOLUTION now has an explicit
  rule that when a patient is in focus and the user's question doesn't
  name a patient, use the focus pid directly — do NOT call
  resolve_patient and do NOT ask the user for a name. The single-patient
  registry block similarly stops saying "call resolve_patient or pass
  this id" and tells the LLM to pass the id directly.

**Pass-rate lift (smoke tier, fixture mode).**

- Before: 1/5 passing (20%); 4 failures on "Please provide the patient's
  name" / clarify-style responses.
- After: 1/5 passing (20%) — but the *failure mode shifted entirely*.
  The four formerly-clarifying cases (smoke-002, smoke-003, smoke-004,
  smoke-005) now route to `agent`, call a tool, and fail on substantive
  grounds: CareTeam-gate denials ("I don't see this patient on your
  panel"). That is the systemic CareTeam-fixture-loader bug tracked in
  issue 019, not a clarify regression.
- smoke-001 still passes; no new failures introduced.

**Files changed.**

- `agent/src/copilot/graph.py` — `_route_after_classifier` helper;
  classifier_node uses it on both happy and exception paths; reads
  `patient_id` and `focus_pid` from state.
- `agent/src/copilot/prompts.py` — PATIENT RESOLUTION rule for focus-bound
  no-name questions; single-patient registry block reworded to be
  directive about using the bound pid.
- `agent/tests/test_clarify_routing.py` (new, 7 cases) — covers the
  helper's branches: cold-start clarify, low-confidence clarify, bound
  patient_id forces agent, bound focus_pid forces agent, both bound +
  low confidence forces agent, high-confidence routes to agent
  regardless, empty-string pids treated as unbound.

**Notes for next iteration.**

- The remaining smoke failures are all CareTeam-gate denials — issue 019
  fixes those at the fixture / lookup layer.
- Pre-existing ruff errors in `graph.py` (RUF001 in the citation regex,
  RUF100 on legacy noqa comments, one E501 line in clarify_node) were
  not touched by this fix; they predate the issue.

## Blocked by

None — can start immediately. Independent of all eval-scaffolding slices.

## User stories addressed

- User story 22
