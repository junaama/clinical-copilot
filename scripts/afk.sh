#!/bin/bash
set -eo pipefail

# Parallel AFK runner.
#
# Spawns N worker processes; each worker repeatedly invokes a fresh Claude
# session. Each session claims exactly one task by atomically renaming the
# issue file into issues/in-progress/, works it, then moves it to issues/done/
# (or back to issues/ if blocked). The atomic mv is the lock: if two workers
# race for the same task, one mv succeeds and the other fails because the
# source path no longer exists.
#
# Task eligibility honours `depends-on` frontmatter — see
# scripts/eligible_issues.py and the format docs in scripts/prompt.md.
#
# Usage: $0 <parallelism> [max_iterations_per_worker]

if [ -z "$1" ]; then
  echo "Usage: $0 <parallelism> [max_iterations_per_worker]"
  exit 1
fi

PARALLELISM="$1"
MAX_ITER="${2:-50}"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

LOGDIR="$REPO_ROOT/logs/afk-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOGDIR"
mkdir -p issues/in-progress

# Recover stale claims from any previous run that crashed mid-task.
# Safe ONLY when no other afk.sh run is currently active.
shopt -s nullglob
for f in issues/in-progress/*.md; do
  echo "[recover] returning stale claim to pool: $f"
  mv "$f" "issues/$(basename "$f")"
done
shopt -u nullglob

# jq filter to extract streaming text from assistant messages.
stream_text='select(.type == "assistant").message.content[]? | select(.type == "text").text // empty | gsub("\n"; "\r\n") | . + "\r\n\n"'
# jq filter to extract final result string.
final_result='select(.type == "result").result // empty'

snapshot_state() {
  python3 "$REPO_ROOT/scripts/eligible_issues.py" --json
}

run_worker() {
  local id="$1"
  local logfile="$LOGDIR/worker-$id.log"

  for ((iter=1; iter<=MAX_ITER; iter++)); do
    local state elig_count blocked_count inprog_count
    state=$(snapshot_state)
    elig_count=$(jq '.eligible | length' <<<"$state")
    blocked_count=$(jq '.blocked | length' <<<"$state")
    inprog_count=$(jq '.in_progress | length' <<<"$state")

    if [ "$elig_count" = "0" ] && [ "$blocked_count" = "0" ] && [ "$inprog_count" = "0" ]; then
      echo "[worker-$id] no tasks remain. exiting." | tee -a "$logfile"
      return 0
    fi

    if [ "$elig_count" = "0" ] && [ "$inprog_count" = "0" ] && [ "$blocked_count" != "0" ]; then
      echo "[worker-$id] DEADLOCK: $blocked_count blocked task(s), nothing in progress, nothing eligible. exiting." | tee -a "$logfile"
      python3 "$REPO_ROOT/scripts/eligible_issues.py" | tee -a "$logfile"
      return 1
    fi

    if [ "$elig_count" = "0" ]; then
      echo "[worker-$id] no eligible tasks (peers still working on $inprog_count, $blocked_count waiting on deps). sleeping 15s." | tee -a "$logfile"
      sleep 15
      continue
    fi

    local jsonfile="$LOGDIR/worker-$id.iter-$iter.jsonl"
    local commits prompt eligible_content blocked_summary in_progress_list full_prompt

    commits=$(git log -n 5 --format="%H%n%ad%n%B---" --date=short 2>/dev/null || echo "No commits found")
    prompt=$(cat scripts/prompt.md)

    eligible_content=$(jq -r '.eligible[] | "=== " + .basename + " ===\n" + .content + "\n"' <<<"$state")
    blocked_summary=$(jq -r '
      if (.blocked | length) == 0 then "(none)"
      else
        [.blocked[] | "  " + .basename + " (waiting on: " + (.missing_deps | join(", ")) + ")"]
        | join("\n")
      end
    ' <<<"$state")
    in_progress_list=$(jq -r '
      if (.in_progress | length) == 0 then "(none)"
      else (.in_progress | join(", "))
      end
    ' <<<"$state")

    full_prompt="Worker ID: $id (iteration $iter of $MAX_ITER)

Previous commits:
$commits

ELIGIBLE issues you may claim (deps satisfied):
$eligible_content

BLOCKED issues (dep not yet in issues/done/ — DO NOT pick):
$blocked_summary

Currently claimed by other workers (DO NOT pick): $in_progress_list

$prompt"

    echo "[worker-$id] iter $iter starting (eligible=$elig_count blocked=$blocked_count inprog=$inprog_count)" | tee -a "$logfile"

    docker sandbox run claude . -- \
      --verbose \
      --dangerously-skip-permissions \
      --print \
      --output-format stream-json \
      "$full_prompt" \
      | grep --line-buffered '^{' \
      | tee "$jsonfile" \
      | jq --unbuffered -rj "$stream_text" \
      | sed -u "s/^/[worker-$id] /" \
      | tee -a "$logfile"

    local result
    result=$(jq -r "$final_result" "$jsonfile" 2>/dev/null || echo "")

    if [[ "$result" == *"<promise>NO MORE TASKS</promise>"* ]]; then
      # Could be true completion or a lost claim race. Loop top will
      # disambiguate by re-snapshotting state.
      echo "[worker-$id] iter $iter reported NO MORE TASKS. rechecking." | tee -a "$logfile"
      continue
    fi
  done

  echo "[worker-$id] hit MAX_ITER=$MAX_ITER. exiting." | tee -a "$logfile"
}

pids=()
cleanup() {
  echo
  echo "[afk] received signal, killing workers: ${pids[*]}"
  for pid in "${pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
}
trap cleanup INT TERM

for ((i=1; i<=PARALLELISM; i++)); do
  run_worker "$i" &
  pids+=($!)
done

echo "[afk] launched $PARALLELISM workers (pids: ${pids[*]}). logs: $LOGDIR"

exit_code=0
for pid in "${pids[@]}"; do
  if ! wait "$pid"; then
    exit_code=1
  fi
done

echo "[afk] all workers complete. logs: $LOGDIR"
exit "$exit_code"
