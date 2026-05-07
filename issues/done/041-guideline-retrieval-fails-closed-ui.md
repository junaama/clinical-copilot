## Parent PRD

`issues/prd.md`

## What to build

Turn guideline retrieval failures into a safe, corpus-bound UI state. When
guideline evidence cannot be retrieved, the answer should state that evidence
is unavailable, avoid fallback-from-memory medical guidance, show the guideline
route/fallback state, and keep technical details out of the main clinical
answer.

## Acceptance criteria

- [ ] Guideline retrieval failures produce a user-facing corpus-bound
      limitation rather than uncited medical guidance.
- [ ] Guideline answers render route metadata that distinguishes guideline
      retrieval from chart FHIR reads.
- [ ] Retrieved guideline answers still render guideline source chips when
      evidence exists.
- [ ] Raw internal messages such as missing active user context, worker names,
      or HTTP statuses are hidden from the main answer.
- [ ] Backend tests cover failed/empty retrieval behavior.
- [ ] Frontend tests cover the guideline failure UI state.

## Blocked by

- Blocked by `issues/039-route-metadata-badge-contract.md`

## User stories addressed

- User story 8
- User story 9
- User story 13
- User story 16
- User story 31
- User story 33
- User story 37
