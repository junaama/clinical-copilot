# 001 — Agent pytest fails with broken `.venv` symlinks

## Symptom

Running any of these from `agent/` fails with `pytest` not found or a broken
interpreter error:

```
uv run pytest -q
.venv/bin/pytest -q
.venv/bin/python -m pytest -q
```

Typical error chain:

```
warning: Ignoring existing virtual environment linked to non-existent
Python interpreter: .venv/bin/python3 -> python
...
error: Failed to spawn: `pytest`
  Caused by: No such file or directory (os error 2)
```

Or, after a partial cleanup:

```
error: Broken virtual environment `.../agent/.venv`: `pyvenv.cfg` is missing
```

Or, when running the venv binary directly:

```
(eval): no such file or directory: .venv/bin/python
```

even though `ls .venv/bin/` shows `python`, `python3`, `python3.12`, and
`pytest` are present.

## Root cause

The committed/cached `.venv` was created on a different machine (likely a
Linux container — its symlinks pointed at
`/home/agent/.local/share/uv/python/cpython-3.12.12-linux-aarch64-gnu/...`).
On this Mac that target does not exist, so:

- `ls -la .venv/bin/python` shows the symlink with its dead target.
- The shell can stat the symlink (so `ls` succeeds) but cannot exec it,
  which is why `.venv/bin/python -V` fails with "no such file or directory."
- `uv run` detects the missing interpreter, tries to recreate the venv,
  and fails to fully clean the directory because some site-packages
  subdirs are not empty (`Directory not empty` errors during `rm -rf
  .venv/lib/python3.12/site-packages/...`).
- A subsequent `uv venv --clear` may succeed but the next `uv pip install
  -e ".[dev]"` re-resolves the python and overwrites the symlink target
  back to a path that doesn't exist on this machine — so the next
  `pytest` invocation breaks again.

The macOS path also had a quirk: `readlink -f .venv/bin/python` resolved
to `/System/Volumes/Data/home/agent/...`, which is the firmlink for
`/home`. That's a red herring — the actual problem is the dead Linux
target, not the firmlink.

## Fix (the recipe that worked)

1. Remove the venv. The `Directory not empty` failures sometimes need a
   second pass after a small wait, because a uv lockfile may still be
   open:

   ```
   rm -rf agent/.venv
   sleep 1
   rm -rf agent/.venv     # second pass if first hit ENOTEMPTY
   ls agent/.venv         # should report "No such file or directory"
   ```

2. Recreate the venv with an **explicit, machine-local Python** instead
   of letting uv pick:

   ```
   cd agent
   uv venv --clear --python /Users/macbook/.local/share/uv/python/cpython-3.12.13-macos-aarch64-none/bin/python3.12
   ls -la .venv/bin/python
   # Expect a symlink whose target starts with /Users/macbook/...
   ```

   Discover the Mac-local Python with:

   ```
   ls /Users/macbook/.local/share/uv/python | grep macos
   ```

3. Install the project with the dev extras using uv pip directly. Do
   **not** use `uv sync` here — `uv sync` re-resolves the interpreter
   and can blow away the freshly-correct symlinks.

   ```
   VIRTUAL_ENV=$PWD/.venv uv pip install -e ".[dev]"
   ```

4. Run tests with the venv's Python explicitly. Avoid `.venv/bin/pytest`
   because it carries a hard-coded shebang that points at the venv's
   `python3` symlink, which other commands keep clobbering. Use:

   ```
   .venv/bin/python3.12 -m pytest tests/<file>.py -q
   ```

   The full path `agent/.venv/bin/python3.12` is the actual Mac binary
   created in step 2 and is the most stable handle.

## Verification

After the fix, this command runs to completion:

```
cd agent
.venv/bin/python3.12 -m pytest tests/ -q
```

Expected output ends with something like:

```
3 failed, 892 passed, 33 skipped, 4 deselected in ~13s
```

The 3 failures (`test_seed_careteam` ×2, `test_token_crypto_lifespan` ×1)
are pre-existing environment-harness failures unrelated to this fix and
documented in prior commit messages. They reproduce on `main`.

## What does NOT work

- `uv run pytest -q` — repeatedly tries to recreate the venv and trips
  on the not-empty cleanup error. Even when it gets past that, the next
  invocation can put the symlinks back into a bad state.
- `uv sync --extra dev` — re-resolves the interpreter and rewrites the
  venv symlinks, breaking step 2's explicit-Python pin. Use
  `uv pip install -e ".[dev]"` instead.
- `.venv/bin/pytest` — fails with `bad interpreter` because the shebang
  points at a stale `python3` symlink. Use
  `.venv/bin/python3.12 -m pytest` instead.
- `.venv/bin/pip` — uv-created venvs do not ship pip by default. Use
  `uv pip install` (with `VIRTUAL_ENV` set) instead.

## Frontend tests

The Vite/Vitest stack in `copilot-ui/` has its own version of this
problem when `node_modules/` was populated on a Linux container —
`rollup`'s native `.node` binary is platform-specific and a Linux build
won't load on macOS. The fix mirrors the Python recipe:

```
cd copilot-ui
rm -rf node_modules package-lock.json
npm install --silent
npm test -- --run
```

After this, `npm test` runs to completion — last verified with
113 passed in ~1.5s.

## When this happens

You'll see this most often on the first test run after:

- Cloning the repo on a Mac when `.venv/` was committed by accident.
- Switching machines (Mac ↔ Linux/CI container) on a worktree that has
  a populated `.venv/`.
- A previous AFK / agent run that recreated the venv inside a sandbox
  with a different OS.

The symptom is "the venv looks fine to `ls` but nothing inside it can
execute." Whenever you see that pattern, jump straight to step 1 above
instead of trying to repair in place.
