## Parent PRD

`issues/prd.md`

## What to build

Make the documented pre-push install command in `W2_ARCHITECTURE.md`
line 905 actually work. Today the line says
`cp hooks/pre-push .git/hooks/pre-push` but no `hooks/` directory
exists. The real gate logic lives at
`scripts/eval-gate-prepush.sh`.

Create `hooks/pre-push` as a thin POSIX-sh wrapper that delegates to
`scripts/eval-gate-prepush.sh` (single source of truth — see
PRD §Implementation Decisions › Pre-Push Hook). Update
`W2_ARCHITECTURE.md` and `agent/README.md` so install instructions
and file layout agree. Land a hook-level test that exercises the
non-zero exit path on a known-bad commit.

## Acceptance criteria

- [ ] `hooks/pre-push` exists, is executable, and execs
      `scripts/eval-gate-prepush.sh "$@"` with non-zero exit
      forwarded
- [ ] `cp hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push`
      installs a working gate end-to-end
- [ ] `W2_ARCHITECTURE.md` line ~905 and the gate-tier table at
      ~644 reference the canonical `hooks/pre-push` source path; a
      one-paragraph note clarifies the wrapper-over-script
      relationship
- [ ] `agent/README.md` references `hooks/pre-push`, not the bare
      script path
- [ ] Hook-level test (`agent/tests/scripts/test_pre_push_hook.sh`
      or pytest with `subprocess`) asserts non-zero exit on a
      deliberately-broken extraction fixture and zero exit on a
      clean fixture
- [ ] The committed wrapper and all docs agree on the top-level
      `scripts/eval-gate-prepush.sh` path; no stale alternate path
      references remain

## Blocked by

None - can start immediately.

## User stories addressed

Reference by number from the parent PRD:

- User story 6
- User story 7
- User story 8
- User story 17
