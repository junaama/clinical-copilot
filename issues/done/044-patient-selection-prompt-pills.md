## Parent PRD

`issues/prd.md`

## What to build

Replace patient-selection auto-briefing with explicit contextual prompt pills.
Selecting a patient should focus that patient and populate prompt pills such as
"Get brief on patient", "Get medications on patient", and "Overnight trends".
No chart brief should be generated merely from selecting a patient; clicking a
pill is the explicit user action that sends a prompt.

## Acceptance criteria

- [ ] Selecting a patient focuses that patient without inserting an automatic
      chart brief into the transcript.
- [ ] Patient-focused prompt pills appear after selection and are scoped to the
      selected patient.
- [ ] Prompt pills include at least brief, medications, and overnight-trends
      options.
- [ ] Clicking a pill sends a user-visible prompt and starts the corresponding
      chat turn.
- [ ] Conversation titles and history distinguish explicit pill-triggered
      prompts from prior auto-generated brief behavior.
- [ ] Frontend tests cover patient focus, pill rendering, and pill-triggered
      prompt submission.

## Blocked by

- Blocked by `issues/043-no-patient-welcome-composer-gating.md`

## User stories addressed

- User story 22
- User story 24
- User story 25
- User story 26
- User story 35
- User story 37
