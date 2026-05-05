# ISSUES

You are ONE worker in a parallel pool. Other workers run concurrently against the same `issues/` folder.

Your prompt context contains three task lists, pre-computed by the runner:

- **ELIGIBLE issues** — deps satisfied, free to claim. Pick from here.
- **BLOCKED issues** — `depends-on` frontmatter references something not yet in `issues/done/`. **DO NOT pick.**
- **Currently claimed by other workers** — already in `issues/in-progress/`. **DO NOT pick.**

You will work on the AFK issues only, not the HITL ones.

You've also been passed the last few commits. Review these to understand what work has already been done.

If the ELIGIBLE list is empty, output `<promise>NO MORE TASKS</promise>` and stop. The runner decides whether that means "all done" or "wait for peers" — that's not your concern.

# TASK SELECTION

Pick the next task **from the ELIGIBLE list only**. Prioritize in this order:

1. Critical bugfixes
2. Development infrastructure (tests, types, dev scripts — precursors to building features)
3. Tracer bullets for new features (tiny end-to-end slice through all layers, then expand)
4. Polish and quick wins
5. Refactors

# DEPENDENCY FRONTMATTER

Issue files may declare dependencies in YAML frontmatter at the top of the file. The runner uses this to compute eligibility — you don't need to evaluate it yourself, but when you author a new issue or split work, use this format:

```
---
depends-on:
  - 001-some-task
  - 002-other-task
---
# Title and body...
```

Inline list and single-value forms are also accepted:

```
---
depends-on: [001-some-task, 002-other-task]
---
```

```
---
depends-on: 001-some-task
---
```

Dependency tokens are basenames **without** the `.md` extension. A dep is satisfied iff `issues/done/<dep>.md` exists.

# CLAIM THE TASK (CRITICAL — DO THIS BEFORE ANYTHING ELSE)

Before reading the task in detail, exploring the repo, or doing any work, **atomically claim the task** by moving the file into `issues/in-progress/`:

```
mv issues/<task>.md issues/in-progress/<task>.md
```

The `mv` is your lock:

- If `mv` succeeds, you own the task. Proceed.
- If `mv` fails (`No such file or directory`), another worker already claimed it. Pick a different task and try again.
- If you cannot claim ANY task (every candidate's `mv` fails), output `<promise>NO MORE TASKS</promise>` and stop.

Do not begin exploration or implementation until the `mv` succeeds. The claim is the lock.

# EXPLORATION

Once claimed, explore the repo as needed for the task.

# IMPLEMENTATION

Use /tdd to complete the task.

# FEEDBACK LOOPS

Before committing, run the feedback loops:

- `npm run test` to run the tests
- `npm run typecheck` to run the type checker

or if in python namespace:
- `uv run uvicorn copilot.server:app --reload --port 8000`
- `uv run pytest -q`
- `uv run ruff check src tests`

# COMMIT

Make a git commit. The commit message must:

1. Include key decisions made
2. Include files changed
3. Blockers or notes for next iteration

# RELEASE THE TASK

You **must** move the file out of `issues/in-progress/` before exiting — leaving it there blocks future workers.

- **Completed:** `mv issues/in-progress/<task>.md issues/done/<task>.md`
- **Blocked / partial:** append a note to the file describing what was done and what remains, then return it to the pool: `mv issues/in-progress/<task>.md issues/<task>.md`

NEVER exit with the file still in `issues/in-progress/`.

# FINAL RULES

- ONLY WORK ON A SINGLE TASK per invocation.
- The claim/release dance is non-negotiable — it is the only thing preventing duplicate work across parallel workers.
