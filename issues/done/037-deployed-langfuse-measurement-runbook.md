## Parent PRD

`issues/w2-submission-pass-prd.md`

## What to build

Define and verify the deployed-first operational measurement path for the
Week 2 submission. The run should exercise representative deployed flows and
collect Langfuse trace evidence for token counts, model names, tool sequence,
supervisor handoffs, cost signals, latency, retrieval behavior, and extraction
confidence without logging raw PHI.

## Acceptance criteria

- [ ] A deployed smoke procedure covers lab upload, intake upload, evidence
      retrieval, and a Week 1 regression prompt.
- [ ] The procedure identifies the required deployed app URL, demo account
      context, fixture documents, and prompts to use.
- [ ] The procedure explains how to find the corresponding Langfuse traces.
- [ ] The trace data checklist includes token counts, model names, tool
      sequence, supervisor handoffs, latency spans, cost estimates, retrieval
      hits, and extraction confidence.
- [ ] The procedure includes a PHI-safety check for logs/traces using synthetic
      data only.
- [ ] The procedure can be run manually without changing production code.
- [ ] At least one dry run confirms the needed trace fields are available or
      identifies any missing observability field as a follow-up.

## Blocked by

- Blocked by `issues/031-upload-bbox-response-contract.md`
- Blocked by `issues/032-source-tabs-with-image-overlay.md`
- Blocked by `issues/033-browser-pdf-source-viewer.md`
- Blocked by `issues/034-backend-shaped-intake-rendering.md`
- Blocked by `issues/035-document-fact-safety-policy-hardening.md`
- Blocked by `issues/036-corpus-bound-evidence-policy-hardening.md`

## User stories addressed

- User story 20
- User story 28
- User story 29
- User story 30
