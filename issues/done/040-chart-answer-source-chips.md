## Parent PRD

`issues/prd.md`

## What to build

Make chart answers visibly cite their chart sources. A medication follow-up
should return citation metadata for the supporting chart resources, render
human-readable source chips in the answer, and allow the user to inspect or
highlight the relevant source area when possible.

## Acceptance criteria

- [ ] A chart medication follow-up returns citation metadata for supported
      medication claims.
- [ ] Citation chips render for chart answers when citations are present and
      citation display is enabled.
- [ ] Source chip labels are human-readable and avoid opaque-only identifiers.
- [ ] Missing chart fields are represented as missing in returned source data,
      not as definitive absence.
- [ ] Clicking a chart source chip triggers the existing source/highlight path
      when a supported card target exists.
- [ ] Backend citation contract tests and frontend rendering tests cover the
      medication follow-up path.

## Blocked by

- Blocked by `issues/039-route-metadata-badge-contract.md`

## User stories addressed

- User story 3
- User story 4
- User story 5
- User story 6
- User story 7
- User story 11
- User story 31
- User story 36
- User story 37
