#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RALPH_V2="$(cd "$SCRIPT_DIR/.." && pwd)/ralph-wiggum-v2.sh"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT

cat > "$TEMP_DIR/tasks.md" <<'EOF'
## Tasks

- [ ] 1.0 Parent task
  - [ ] 1.1 First task
  - [ ] 1.2 Second task
  - [ ] 1.3 Third task

## Verification Criteria

- [ ] Verification should not run in a bounded batch
EOF

original_tasks="$(cat "$TEMP_DIR/tasks.md")"
output="$(
  bash "$RALPH_V2" "$TEMP_DIR/tasks.md" \
    --print-only \
    --max-retries 0 \
    --max-tasks 2
)"

task_count="$(grep -c "═══ Task [0-9]" <<<"$output")"
[[ "$task_count" -eq 2 ]]
grep -q "Task limit reached after 2 attempted tasks" <<<"$output"
grep -q "Skipping verification because the task limit was reached" <<<"$output"
if grep -q "Verification Phase" <<<"$output"; then
  echo "Verification ran after the task limit was reached" >&2
  exit 1
fi
[[ "$(cat "$TEMP_DIR/tasks.md")" == "$original_tasks" ]]

if bash "$RALPH_V2" "$TEMP_DIR/tasks.md" --print-only --max-tasks 0 >"$TEMP_DIR/invalid.log" 2>&1; then
  echo "Ralph accepted --max-tasks 0" >&2
  exit 1
fi
grep -q -- "--max-tasks must be a positive integer" "$TEMP_DIR/invalid.log"

unbounded_output="$(
  bash "$RALPH_V2" "$TEMP_DIR/tasks.md" \
    --print-only \
    --max-retries 0
)"
unbounded_task_count="$(grep -c "═══ Task [0-9]" <<<"$unbounded_output")"
[[ "$unbounded_task_count" -eq 3 ]]

echo "Ralph max-tasks integration test passed"
