#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RALPH_V2="$(cd "$SCRIPT_DIR/.." && pwd)/ralph-wiggum-v2.sh"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT

mkdir -p "$TEMP_DIR/bin" "$TEMP_DIR/work"
touch "$TEMP_DIR/copilot-sdk.js"

cat > "$TEMP_DIR/bin/copilot" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$RALPH_TEST_COPILOT_ARGS"
echo "VERIFICATION_RESULT: PASS"
EOF

cat > "$TEMP_DIR/bin/node" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$RALPH_TEST_NODE_ARGS"
cat > "$RALPH_TEST_NODE_STDIN"
EOF

chmod +x "$TEMP_DIR/bin/copilot" "$TEMP_DIR/bin/node"

cat > "$TEMP_DIR/work/tasks.md" <<'EOF'
## Tasks

- [x] 1.0 Already complete

## Verification Criteria

- [x] Cleanup is exercised
EOF

export RALPH_TEST_COPILOT_ARGS="$TEMP_DIR/copilot-args.txt"
export RALPH_TEST_NODE_ARGS="$TEMP_DIR/node-args.txt"
export RALPH_TEST_NODE_STDIN="$TEMP_DIR/node-stdin.js"
export RALPH_COPILOT_SDK_PATH="$TEMP_DIR/copilot-sdk.js"

PATH="$TEMP_DIR/bin:$PATH" bash "$RALPH_V2" \
  "$TEMP_DIR/work/tasks.md" >/dev/null

session_id="$(awk '/^--session-id$/{getline; print; exit}' "$RALPH_TEST_COPILOT_ARGS")"
cleanup_session_id="$(grep -E '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' "$RALPH_TEST_NODE_ARGS")"

[[ "$session_id" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]]
[[ "$cleanup_session_id" == "$session_id" ]]
grep -qx -- "--no-remote-export" "$RALPH_TEST_COPILOT_ARGS"
grep -qx -- "--allow-all" "$RALPH_TEST_COPILOT_ARGS"
grep -qx -- "--max-ai-credits" "$RALPH_TEST_COPILOT_ARGS"
grep -qx -- "30" "$RALPH_TEST_COPILOT_ARGS"
if grep -Eq -- "^--(engine|max-budget-usd|permission-mode|allow-all-tools)$" "$RALPH_TEST_COPILOT_ARGS"; then
  echo "Found a removed or superseded CLI flag in the Copilot invocation" >&2
  exit 1
fi
grep -q "client.deleteSession(sessionId)" "$RALPH_TEST_NODE_STDIN"

echo "Copilot session cleanup integration test passed"
