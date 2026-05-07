## Parent PRD

`issues/w2-submission-pass-prd.md`

## What to build

Align the frontend intake results view with the backend Pydantic extraction
shape and connect important intake fields to the source-overlay interaction
when exact bbox paths exist. This keeps the UI honest to the actual backend
contract rather than maintaining a parallel stale TypeScript shape.

## Acceptance criteria

- [ ] Intake demographics render backend-shaped fields such as date of birth,
      gender, phone, address, and emergency contact where present.
- [ ] Intake social history renders backend-shaped smoking, alcohol, drug, and
      occupation fields where present.
- [ ] Intake medications, allergies, family history, and chief concern render
      without requiring frontend-only confidence fields.
- [ ] Source CTAs appear for important intake fields only when exact bbox paths
      exist.
- [ ] Selecting an intake source CTA switches to Source and highlights the
      matching bbox.
- [ ] Existing lab extraction rendering remains unchanged except for shared
      source CTA behavior.
- [ ] Component tests use backend-shaped intake fixtures and cover rendering
      plus source CTA behavior.

## Blocked by

- Blocked by `issues/031-upload-bbox-response-contract.md`
- Blocked by `issues/032-source-tabs-with-image-overlay.md`

## User stories addressed

- User story 2
- User story 5
- User story 25
- User story 26
