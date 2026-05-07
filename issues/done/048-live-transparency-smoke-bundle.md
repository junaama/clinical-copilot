## Parent PRD

`issues/prd.md`

## What to build

Create a final smoke bundle for the route and citation transparency workflow.
The bundle should verify the deployed or fixture-backed app across the core
paths from the PRD: chart brief, cited medication follow-up, guideline
no-evidence or failure, panel state, no-patient state, document source chips,
patient prompt pills, conversation rehydration, and consent explanation.

## Acceptance criteria

- [ ] Smoke coverage includes a chart brief or chart answer with visible route
      metadata.
- [ ] Smoke coverage includes a medication follow-up with visible chart source
      chips.
- [ ] Smoke coverage includes a guideline no-evidence or retrieval-failure
      case that fails closed.
- [ ] Smoke coverage includes panel triage success or safe failure state.
- [ ] Smoke coverage includes the no-patient welcome/composer gating state.
- [ ] Smoke coverage includes patient-selection prompt pills and confirms no
      auto-brief fires on selection.
- [ ] Smoke coverage includes conversation rehydration preserving provenance.
- [ ] Smoke coverage includes document source chips when document evidence is
      available.
- [ ] Smoke coverage includes the OAuth consent explanation.
- [ ] The smoke instructions or tests avoid raw chart-content leakage in logs.

## Blocked by

- Blocked by `issues/040-chart-answer-source-chips.md`
- Blocked by `issues/041-guideline-retrieval-fails-closed-ui.md`
- Blocked by `issues/042-panel-triage-route-failure-state.md`
- Blocked by `issues/043-no-patient-welcome-composer-gating.md`
- Blocked by `issues/044-patient-selection-prompt-pills.md`
- Blocked by `issues/045-conversation-rehydration-provenance.md`
- Blocked by `issues/046-document-source-chips-post-upload.md`
- Blocked by `issues/047-consent-explanation-oauth-handoff.md`

## User stories addressed

- User story 36
- User story 37
- User story 38
