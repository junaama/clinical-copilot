## Parent PRD

`issues/w2-submission-pass-prd.md`

## What to build

Create the final verification bundle for Week 2 submission readiness. This is a
checklist-style vertical slice that validates the product the way a grader will:
visual source grounding, cited document summary, guideline evidence citations,
supervisor trace inspectability, W2 eval gate, graph integration, and deployed
measurement evidence.

## Acceptance criteria

- [ ] A lab PDF upload demonstrates extraction results and visual source
      highlight in the deployed app.
- [ ] An intake form upload demonstrates structured extraction and source
      highlight for at least one important field.
- [ ] A post-upload chat answer cites the uploaded `DocumentReference`.
- [ ] A guideline question answer cites retrieved guideline chunks.
- [ ] A Langfuse trace shows supervisor routing and worker handoffs for a
      document or evidence turn.
- [ ] The W2 50-case eval gate passes.
- [ ] The graph integration test layer passes.
- [ ] Deployed measurement evidence from the runbook is available for latency,
      token, model, and cost reporting.
- [ ] Any remaining submission caveats are explicit and do not contradict the
      system design decisions in the parent PRD.

## Blocked by

- Blocked by `issues/031-upload-bbox-response-contract.md`
- Blocked by `issues/032-source-tabs-with-image-overlay.md`
- Blocked by `issues/033-browser-pdf-source-viewer.md`
- Blocked by `issues/034-backend-shaped-intake-rendering.md`
- Blocked by `issues/035-document-fact-safety-policy-hardening.md`
- Blocked by `issues/036-corpus-bound-evidence-policy-hardening.md`
- Blocked by `issues/037-deployed-langfuse-measurement-runbook.md`

## User stories addressed

- User story 18
- User story 19
- User story 20
- User story 21
- User story 28
- User story 30
