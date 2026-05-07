## Parent PRD

`issues/prd.md`

## What to build

Normalize panel triage into an inspectable product state. Panel triage answers
should advertise the panel route, return safe failure copy when authorization
or data access fails, and keep technical diagnostics available only behind an
explicit debug affordance.

## Acceptance criteria

- [ ] Panel triage responses include route metadata identifying the panel
      route.
- [ ] Panel triage authorization or access failures render a product-safe
      "panel data unavailable" style answer with a next step.
- [ ] Raw internal probe names and HTTP statuses do not appear in the main
      answer.
- [ ] A debug/technical-details affordance can expose route and diagnostic
      fields for development or grading.
- [ ] Backend panel triage tests cover normalized success and failure states.
- [ ] Frontend tests cover route rendering and hidden-by-default technical
      details.

## Blocked by

- Blocked by `issues/039-route-metadata-badge-contract.md`

## User stories addressed

- User story 1
- User story 2
- User story 12
- User story 13
- User story 14
- User story 15
- User story 16
- User story 19
- User story 33
- User story 37
- User story 38
