#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASK_HELPERS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RALPH_PARENT_TASKS="$TASK_HELPERS_DIR/ralph-wiggum-parent-tasks.sh"

TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT
TASK_FILE="$TEMP_DIR/tasks.md"

cat > "$TASK_FILE" <<'EOF'
## Tasks

- [ ] 1.0 Implement feature
  Parent-level notes.
  - [ ] 1.1 Add implementation
  - [ ] 1.2 Add tests

## Verification Criteria

- [ ] Tests pass
EOF

output="$("$RALPH_PARENT_TASKS" "$TASK_FILE" --print-only --max-retries 0)"

task_log="$(sed -n 's/.*Full output saved to: //p' <<<"$output" | head -1)"
grep -q "Your job right now is to implement the entire following parent task" "$task_log"
grep -q -- "- \[ \] 1.1 Add implementation" "$task_log"
grep -q -- "- \[ \] 1.2 Add tests" "$task_log"

parent_count="$(grep -c "═══ Parent task" <<<"$output")"
if [[ "$parent_count" -ne 1 ]]; then
  echo "Expected one parent-task agent call, got $parent_count" >&2
  exit 1
fi

if grep -q -- "--model" <<<"$output"; then
  echo "Model flag should be omitted when --model is not provided" >&2
  exit 1
fi

explicit_model_output="$(
  "$RALPH_PARENT_TASKS" "$TASK_FILE" \
    --print-only \
    --model test-model \
    --max-ai-credits 45
)"

grep -q -- "--model test-model" <<<"$explicit_model_output"
grep -q -- "--max-ai-credits 45" <<<"$explicit_model_output"

if grep -qi "claude" "$RALPH_PARENT_TASKS" "$TASK_HELPERS_DIR/ralph-wiggum-v2.sh"; then
  echo "Ralph scripts must not contain Claude-specific behavior" >&2
  exit 1
fi
