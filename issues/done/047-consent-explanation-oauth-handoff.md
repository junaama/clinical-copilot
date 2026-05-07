## Parent PRD

`issues/prd.md`

## What to build

Add an app-specific consent explanation before or around the standalone OAuth
handoff. The explanation should summarize why Clinical Co-Pilot requests broad
read scopes, why offline access appears in this deployment, and that the app
remains read-only unless a future confirmed write flow is explicitly added.

## Acceptance criteria

- [ ] Standalone login or authorization handoff includes concise
      Clinical Co-Pilot-specific consent context.
- [ ] The copy explains broad read scopes in terms of chart, panel,
      guideline/document, and source-grounding workflows.
- [ ] The copy explains offline access duration/purpose for this deployment
      when offline access is requested.
- [ ] The copy clearly states the current read-only posture.
- [ ] The flow still links to or continues through OpenEMR authorization.
- [ ] Frontend tests cover the presence of the consent explanation.

## Blocked by

None - can start immediately.

## User stories addressed

- User story 27
- User story 28
- User story 29
