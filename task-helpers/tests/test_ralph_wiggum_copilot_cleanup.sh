#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RALPH_V2="$(cd "$SCRIPT_DIR/.." && pwd)/ralph-wiggum-v2.sh"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT
REAL_NODE="$(command -v node)"

mkdir -p "$TEMP_DIR/bin" "$TEMP_DIR/work"
touch "$TEMP_DIR/copilot-sdk.js"

cat > "$TEMP_DIR/bin/copilot" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "--help" ]]; then
  cat <<'HELP'
  --max-ai-credits <n>  Soft AI-credit cap
  --session-id <id>     Start with a specific session ID
  --no-remote-export    Disable remote export
  --allow-all           Enable all permissions
  --no-ask-user         Disable clarification prompts
HELP
  exit 0
fi
if [[ "${1:-}" == "--version" ]]; then
  echo "GitHub Copilot CLI test"
  exit 0
fi
printf '%s\n' "$@" > "$RALPH_TEST_COPILOT_ARGS"
echo "VERIFICATION_RESULT: PASS"
EOF

cat > "$TEMP_DIR/bin/node" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$RALPH_TEST_NODE_ARGS"
cat > "$RALPH_TEST_NODE_STDIN"
echo "RALPH_SESSION_USAGE: session_id=test-session ai_credits=12.5 premium_request_cost=2 user_requests=3 input_tokens=400 output_tokens=100 api_duration_ms=2500"
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
grep -qx -- "2000" "$RALPH_TEST_COPILOT_ARGS"
if grep -Eq -- "^--(engine|max-budget-usd|permission-mode|allow-all-tools)$" "$RALPH_TEST_COPILOT_ARGS"; then
  echo "Found a removed or superseded CLI flag in the Copilot invocation" >&2
  exit 1
fi
grep -q "session.rpc.usage.getMetrics()" "$RALPH_TEST_NODE_STDIN"
grep -q "totalNanoAiu / 1e9" "$RALPH_TEST_NODE_STDIN"
grep -q "client.deleteSession(sessionId)" "$RALPH_TEST_NODE_STDIN"
"$REAL_NODE" --input-type=module --check < "$RALPH_TEST_NODE_STDIN"
grep -R -q "RALPH_SESSION_USAGE: session_id=test-session ai_credits=12.5" "$TEMP_DIR/work/.ralph-logs"
usage_log="$(find "$TEMP_DIR/work/.ralph-logs" -name session-usage.log -type f -print -quit)"
grep -q "RALPH_SESSION_USAGE: session_id=test-session ai_credits=12.5" "$usage_log"
grep -q "session-usage -> $usage_log" "$TEMP_DIR/work/ralph-logs-index.md"

echo "Copilot session cleanup integration test passed"
