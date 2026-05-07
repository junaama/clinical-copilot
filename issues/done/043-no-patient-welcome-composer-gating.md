## Parent PRD

`issues/prd.md`

## What to build

Tighten the no-patient state so the app does not imply a selected chart before
one exists. The welcome state, prompt suggestions, upload widget, composer
placeholder, Send button, and panel shortcuts should all reflect whether the
current context is no-patient, patient-focused, or panel-capable.

## Acceptance criteria

- [ ] Empty-state copy does not say or imply "this patient" before a patient
      is selected.
- [ ] Patient-specific upload and chart prompts are disabled with clear reasons
      until a patient is resolved.
- [ ] Panel-wide prompts are enabled only when the panel route can run without
      patient selection.
- [ ] Composer placeholder text changes based on no-patient, patient-focused,
      or panel-capable context.
- [ ] Send button disabled state is understandable from the visible UI state.
- [ ] Frontend tests cover no-patient copy, disabled controls, and panel prompt
      behavior.

## Blocked by

- Blocked by `issues/039-route-metadata-badge-contract.md`
- Blocked by `issues/042-panel-triage-route-failure-state.md`

## User stories addressed

- User story 17
- User story 18
- User story 19
- User story 20
- User story 21
- User story 34
- User story 37
