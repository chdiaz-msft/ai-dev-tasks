#!/usr/bin/env bash
set -euo pipefail

# ralph-wiggum-v2.sh — Sequential task executor using GitHub Copilot CLI
#
# Usage: ./ralph-wiggum-v2.sh <tasks.md> [options]
#
# Expected markdown format (supports nested parent/subtask structure):
#   ## Tasks
#   - [ ] 1.0 Parent task group
#     - [ ] 1.1 First subtask
#     - [ ] 1.2 Second subtask
#   - [ ] 2.0 Another parent
#     - [ ] 2.1 Subtask
#
#   # Verification criteria
#   [] Check that X works
#   - [ ] Check that Y passes
#
# Options:
#   --model <model>              Copilot model to use (omitted by default)
#   --max-retries <n>            Max retries per task before giving up (default: 2)
#   --max-ai-credits <n>         Soft AI-credit cap per Copilot call (default: 30;
#                                Copilot CLI minimum: 30)
#   --system-prompt-file <path>  Prompt file prepended to each Copilot prompt
#   --selfcorrect, -s            On failure, make an agent call to diagnose and fix
#                                the root cause before retrying (skipped for BLOCKED)
#   --print-only                 Dry run — print tasks without executing
#   --verbose, -v                Verbose debug logging for troubleshooting

# ── Colors & output helpers ──────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

log()     { echo -e "${BLUE}[ralph]${NC} $*"; }
ok()      { echo -e "${GREEN}[ralph]${NC} $*"; }
warn()    { echo -e "${YELLOW}[ralph]${NC} $*"; }
err()     { echo -e "${RED}[ralph]${NC} $*" >&2; }
header()  { echo -e "\n${BOLD}${CYAN}═══ $* ═══${NC}\n"; }
verbose() { $VERBOSE && echo -e "${CYAN}[ralph:debug]${NC} $*" >&2 || true; }

# ── Defaults ─────────────────────────────────────────────────────────────────

MODEL=""              # empty = omit --model and let Copilot choose
MAX_RETRIES=2
MAX_AI_CREDITS=30
PRINT_ONLY=false
VERBOSE=false
SELFCORRECT=false
SYSTEM_PROMPT_FILE=""
TASK_SCOPE="${RALPH_TASK_SCOPE:-leaf}"
COPILOT_SDK_PATH="${RALPH_COPILOT_SDK_PATH:-}"
COPILOT_RUNTIME_PATH="${RALPH_COPILOT_RUNTIME_PATH:-}"

if [[ "$TASK_SCOPE" != "leaf" && "$TASK_SCOPE" != "parent" ]]; then
  err "Invalid RALPH_TASK_SCOPE '$TASK_SCOPE' (expected: leaf | parent)"
  exit 1
fi

# ── Arg parsing ──────────────────────────────────────────────────────────────

TASKS_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)              MODEL="$2"; shift 2 ;;
    --max-retries)        MAX_RETRIES="$2"; shift 2 ;;
    --max-ai-credits)     MAX_AI_CREDITS="$2"; shift 2 ;;
    --system-prompt-file) SYSTEM_PROMPT_FILE="$2"; shift 2 ;;
    --selfcorrect|-s)     SELFCORRECT=true; shift ;;
    --print-only)         PRINT_ONLY=true; shift ;;
    --verbose|-v)         VERBOSE=true; shift ;;
    -h|--help)
      sed -n '3,/^$/p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    -*)
      err "Unknown option: $1"
      exit 1
      ;;
    *)
      if [[ -z "$TASKS_FILE" ]]; then
        TASKS_FILE="$1"
      else
        err "Unexpected argument: $1"
        exit 1
      fi
      shift
      ;;
  esac
done

if [[ -z "$TASKS_FILE" ]]; then
  err "Usage: $0 <tasks.md> [options]"
  exit 1
fi

if [[ ! -f "$TASKS_FILE" ]]; then
  err "Tasks file not found: $TASKS_FILE"
  exit 1
fi

# ── Copilot validation & preflight ───────────────────────────────────────────

if ! [[ "$MAX_AI_CREDITS" =~ ^[0-9]+$ ]] || [[ "$MAX_AI_CREDITS" -lt 30 ]]; then
  err "--max-ai-credits must be an integer of at least 30"
  exit 1
fi

if ! $PRINT_ONLY && ! command -v copilot >/dev/null 2>&1; then
  err "GitHub Copilot CLI is required on PATH (https://docs.github.com/copilot/how-tos/copilot-cli)"
  exit 1
fi

if ! $PRINT_ONLY && ! command -v node >/dev/null 2>&1; then
  err "Node.js is required to clean up completed Copilot sessions"
  exit 1
fi

TASKS_FILE="$(realpath "$TASKS_FILE")"
TASKS_DIR="$(dirname "$TASKS_FILE")"

# ── Dry-run file protection ──────────────────────────────────────────────────
# In print-only mode, we need to mark tasks [x] to advance the loop,
# but we don't want to mutate the real file. Backup and restore on exit.

if $PRINT_ONLY; then
  TASKS_FILE_BACKUP="$(mktemp)"
  cp "$TASKS_FILE" "$TASKS_FILE_BACKUP"
  trap 'cp "$TASKS_FILE_BACKUP" "$TASKS_FILE"; rm -f "$TASKS_FILE_BACKUP"' EXIT
fi

# ── Auto-discover system prompt file ─────────────────────────────────────────

if [[ -z "$SYSTEM_PROMPT_FILE" ]]; then
  # Look for process-task-list.prompt.md relative to the repo root
  repo_root="$(git -C "$TASKS_DIR" rev-parse --show-toplevel 2>/dev/null || echo "")"
  if [[ -n "$repo_root" ]]; then
    candidate="$repo_root/task-helpers/process-task-list.prompt.md"
    if [[ -f "$candidate" ]]; then
      SYSTEM_PROMPT_FILE="$candidate"
      log "Auto-discovered system prompt: $SYSTEM_PROMPT_FILE"
    fi
  fi
fi

# Load and prepare the system prompt content (strip YAML frontmatter)
SYSTEM_PROMPT_CONTENT=""
if [[ -n "$SYSTEM_PROMPT_FILE" && -f "$SYSTEM_PROMPT_FILE" ]]; then
  # Strip YAML frontmatter (--- ... ---)
  SYSTEM_PROMPT_CONTENT=$(awk '
    BEGIN { in_frontmatter=0; found_end=0 }
    NR==1 && /^---[[:space:]]*$/ { in_frontmatter=1; next }
    in_frontmatter && /^---[[:space:]]*$/ { in_frontmatter=0; found_end=1; next }
    !in_frontmatter && found_end { print }
    !in_frontmatter && !found_end && NR>1 { print }
  ' "$SYSTEM_PROMPT_FILE")

  # Override the "wait for user approval" behavior for headless mode
  if [[ "$TASK_SCOPE" == "parent" ]]; then
    SYSTEM_PROMPT_CONTENT="$SYSTEM_PROMPT_CONTENT

## Headless Mode Override
You are running in HEADLESS/AUTONOMOUS mode via the ralph-wiggum task runner.
- Do NOT wait for user approval while completing the assigned parent task.
- Do NOT ask the user for permission — there is no interactive user.
- Complete every incomplete subtask under the assigned parent task, marking each one [x].
- When all subtasks are resolved, mark the assigned parent task [x] and stop.
- Update the Relevant Files section if you create or modify files."
  else
    SYSTEM_PROMPT_CONTENT="$SYSTEM_PROMPT_CONTENT

## Headless Mode Override
You are running in HEADLESS/AUTONOMOUS mode via the ralph-wiggum task runner.
- Do NOT wait for user approval between subtasks.
- Do NOT ask the user for permission — there is no interactive user.
- Complete ONLY the assigned subtask, mark it [x] in the task file, and stop.
- If all subtasks under a parent are now [x], also mark the parent [x].
- Update the Relevant Files section if you create or modify files."
  fi
fi

# ── Task parsing via Python CLI ─────────────────────────────────────────────

# Get path to the Python CLI task parser (relative to this script)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASK_PARSER="$SCRIPT_DIR/task_parser.py"

# task_parser.py uses only the Python standard library. Prefer the repository's
# uv workflow when available, but do not require uv on machines with Python.
if command -v uv >/dev/null 2>&1; then
  PARSER_COMMAND=(uv run)
elif command -v python3 >/dev/null 2>&1; then
  PARSER_COMMAND=(python3)
elif command -v python >/dev/null 2>&1; then
  PARSER_COMMAND=(python)
else
  err "task_parser.py requires uv, python3, or python on PATH"
  exit 1
fi

# ── Parser wrapper with error handling ──────────────────────────────────────
# Captures stderr, logs on failure, prevents set -e from killing the script.
# Exit codes from task_parser.py:
#   0 = success
#   1 = "no result" (e.g., no incomplete tasks for next-task)
#   3 = error (file not found, parse error, etc.)

run_parser() {
  local subcmd="$1"; shift
  local stdout_output stderr_output exit_code
  local stderr_file
  stderr_file=$(mktemp)

  verbose "run_parser: ${PARSER_COMMAND[*]} task_parser.py $subcmd $*"

  # Capture stdout and stderr separately; prevent set -e from killing us
  stdout_output=$("${PARSER_COMMAND[@]}" "$TASK_PARSER" "$subcmd" "$@" 2>"$stderr_file") && exit_code=0 || exit_code=$?
  stderr_output=$(<"$stderr_file")
  rm -f "$stderr_file"

  verbose "run_parser: $subcmd exited $exit_code"
  if [[ -n "$stderr_output" ]]; then
    verbose "run_parser: $subcmd stderr: $stderr_output"
  fi

  # Log errors for non-expected exit codes (0=success, 1=no-result are expected)
  if [[ $exit_code -ne 0 && $exit_code -ne 1 ]]; then
    err "task_parser '$subcmd' failed (exit $exit_code)"
    if [[ -n "$stderr_output" ]]; then
      err "  stderr: $stderr_output"
    fi
    if [[ -n "$stdout_output" ]]; then
      err "  stdout: $stdout_output"
    fi
  fi

  echo "$stdout_output"
  return $exit_code
}

# Get the next incomplete LEAF task (skips parents that have subtasks)
# Returns: line_number|parent_text|task_text
#   Exit 0 = found a task, Exit 1 = no incomplete tasks, Exit 3 = error
get_next_task() {
  run_parser next-task "$TASKS_FILE"
}

# Get the next incomplete TOP-LEVEL parent task and its complete markdown block.
# Returns:
#   first line: start_line|end_line|parent_text
#   remaining lines: full parent task context
get_next_parent() {
  run_parser next-parent "$TASKS_FILE"
}

# Mark a task as failed by line number and description (for stable addressing)
mark_task_failed() {
  local line_num="$1"
  local task_desc="$2"
  run_parser mark-failed "$TASKS_FILE" --line "$line_num" --match "$task_desc"
}

# Mark the active work item failed. Parent mode marks all remaining tasks in
# the subtree so the parent is not selected again on the next loop iteration.
mark_work_item_failed() {
  local line_num="$1"
  local task_desc="$2"
  if [[ "$TASK_SCOPE" == "parent" ]]; then
    run_parser mark-subtree-failed "$TASKS_FILE" --line "$line_num" --match "$task_desc"
  else
    mark_task_failed "$line_num" "$task_desc"
  fi
}

# Check if a specific line is now marked complete [x]
is_task_marked_complete() {
  local line_num="$1"
  local task_desc="$2"
  run_parser is-complete "$TASKS_FILE" --line "$line_num" --match "$task_desc"
}

# Check that every leaf task within a parent group is complete or failed.
is_parent_task_resolved() {
  local line_num="$1"
  local task_desc="$2"
  run_parser is-subtree-resolved "$TASKS_FILE" --line "$line_num" --match "$task_desc"
}

# Auto-complete parent tasks whose subtasks are all [x]
auto_complete_parents() {
  run_parser auto-complete-parents "$TASKS_FILE"
}

# Extract the verification section content (supports h1, h2, h3 headers)
get_verification_section() {
  run_parser verification-section "$TASKS_FILE"
}

# Count total and completed tasks (leaves only)
count_tasks() {
  run_parser count "$TASKS_FILE"
}

# Locate the SDK shipped with Copilot CLI. The optional environment override
# supports standalone or nonstandard Copilot installations.
find_copilot_sdk() {
  if [[ -n "$COPILOT_SDK_PATH" && -f "$COPILOT_SDK_PATH" ]]; then
    if [[ -z "$COPILOT_RUNTIME_PATH" ]]; then
      COPILOT_RUNTIME_PATH="$(command -v copilot)"
    fi
    return 0
  fi

  local copilot_bin copilot_dir resolved_bin resolved_dir npm_root candidate
  local runtime_candidate runtime_name
  local os_name architecture package_platform cache_base
  copilot_bin="$(command -v copilot)"
  copilot_dir="$(dirname "$copilot_bin")"
  resolved_bin="$(readlink -f "$copilot_bin" 2>/dev/null || true)"
  resolved_dir=""
  if [[ -n "$resolved_bin" ]]; then
    resolved_dir="$(dirname "$resolved_bin")"
  fi

  os_name="$(uname -s)"
  architecture="$(uname -m)"
  case "$architecture" in
    x86_64|amd64) architecture="x64" ;;
    aarch64|arm64) architecture="arm64" ;;
  esac

  cache_base=""
  case "$os_name" in
    MINGW*|MSYS*|CYGWIN*)
      package_platform="win32-$architecture"
      runtime_name="copilot.exe"
      if [[ -n "${LOCALAPPDATA:-}" ]]; then
        if command -v cygpath >/dev/null 2>&1; then
          cache_base="$(cygpath -u "$LOCALAPPDATA")/copilot/pkg/$package_platform"
        else
          cache_base="$LOCALAPPDATA/copilot/pkg/$package_platform"
        fi
      fi
      ;;
    Darwin)
      package_platform="darwin-$architecture"
      runtime_name="copilot"
      cache_base="$HOME/Library/Caches/copilot/pkg/$package_platform"
      ;;
    Linux)
      package_platform="linux-$architecture"
      runtime_name="copilot"
      cache_base="${XDG_CACHE_HOME:-$HOME/.cache}/copilot/pkg/$package_platform"
      ;;
  esac

  local -a candidates=()
  if [[ -n "$cache_base" && -d "$cache_base" ]]; then
    local -a cached_sdks=("$cache_base"/*/copilot-sdk/index.js)
    local index
    for ((index=${#cached_sdks[@]} - 1; index>=0; index--)); do
      candidates+=("${cached_sdks[$index]}")
    done
  fi

  candidates+=(
    "$copilot_dir/copilot-sdk/index.js"
    "$copilot_dir/node_modules/@github/copilot/copilot-sdk/index.js"
    "$copilot_dir/../lib/node_modules/@github/copilot/copilot-sdk/index.js"
  )
  if [[ -n "$resolved_dir" && "$resolved_dir" != "$copilot_dir" ]]; then
    candidates+=(
      "$resolved_dir/copilot-sdk/index.js"
      "$resolved_dir/node_modules/@github/copilot/copilot-sdk/index.js"
      "$resolved_dir/../lib/node_modules/@github/copilot/copilot-sdk/index.js"
    )
  fi
  if command -v npm >/dev/null 2>&1; then
    npm_root="$(npm root -g 2>/dev/null || true)"
    if [[ -n "$npm_root" ]]; then
      candidates+=("$npm_root/@github/copilot/copilot-sdk/index.js")
      if [[ -n "${package_platform:-}" && -n "${runtime_name:-}" ]]; then
        runtime_candidate="$npm_root/@github/copilot/node_modules/@github/copilot-$package_platform/$runtime_name"
        if [[ -z "$COPILOT_RUNTIME_PATH" && -f "$runtime_candidate" ]]; then
          COPILOT_RUNTIME_PATH="$(realpath "$runtime_candidate")"
        fi
      fi
    fi
  fi

  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate" ]]; then
      COPILOT_SDK_PATH="$(realpath "$candidate")"
      if [[ -z "$COPILOT_RUNTIME_PATH" ]]; then
        COPILOT_RUNTIME_PATH="$resolved_bin"
      fi
      return 0
    fi
  done

  return 1
}

generate_session_id() {
  if command -v uuidgen >/dev/null 2>&1; then
    uuidgen | tr '[:upper:]' '[:lower:]'
  elif command -v uv >/dev/null 2>&1; then
    uv run python -c 'import uuid; print(uuid.uuid4())'
  elif command -v python3 >/dev/null 2>&1; then
    python3 -c 'import uuid; print(uuid.uuid4())'
  elif command -v python >/dev/null 2>&1; then
    python -c 'import uuid; print(uuid.uuid4())'
  fi
}

delete_copilot_session() {
  local session_id="$1"

  node --input-type=module - "$COPILOT_SDK_PATH" "$session_id" "$COPILOT_RUNTIME_PATH" <<'NODE'
import { pathToFileURL } from "node:url";

const [sdkPath, sessionId, runtimePath] = process.argv.slice(2);
const { CopilotClient, RuntimeConnection } = await import(pathToFileURL(sdkPath).href);
const client = RuntimeConnection
  ? new CopilotClient({
      connection: RuntimeConnection.forStdio({ path: runtimePath }),
    })
  : new CopilotClient();

try {
  await client.start();
  await client.deleteSession(sessionId);
} finally {
  const errors = await client.stop();
  if (errors.length > 0) {
    throw new AggregateError(errors, "Failed to stop the Copilot SDK client cleanly");
  }
}
NODE
}

if ! $PRINT_ONLY; then
  if ! find_copilot_sdk; then
    err "Could not locate Copilot CLI's copilot-sdk/index.js for session cleanup"
    err "Set RALPH_COPILOT_SDK_PATH to the SDK entry point for a nonstandard installation"
    exit 1
  fi
fi

# ── Run Copilot headless ──────────────────────────────────────────────────────
# Copilot runs the prompt non-interactively with autonomous tool use and prints
# the agent response to stdout. The caller parses that output for the
# TASK_STATUS:/VERIFICATION_RESULT: line and checks the task file for [x] marks,
# while a temporary session ID lets the runner clean up saved session state.

run_agent() {
  local prompt="$1"
  local output
  local final_prompt="$prompt"
  local session_id

  if $PRINT_ONLY; then
    log "(dry run) Would send to Copilot:"
    echo ""
    echo "$prompt"
    echo ""
    if [[ -n "$SYSTEM_PROMPT_CONTENT" ]]; then
      log "(dry run) With system prompt prepended into the prompt (${#SYSTEM_PROMPT_CONTENT} chars)"
    fi
    log "(dry run) Invocation: $(agent_cmdline_preview)"
    return 0
  fi

  # Copilot CLI has no system-prompt flag, so prepend the instructions to the
  # prompt. --allow-all prevents permission prompts for tools, paths, and URLs;
  # --no-ask-user prevents clarification prompts in this autonomous workflow.
  if [[ -n "$SYSTEM_PROMPT_CONTENT" ]]; then
    final_prompt="$SYSTEM_PROMPT_CONTENT

$prompt"
  fi

  local -a cmd=(
    copilot
    -p "$final_prompt"
  )
  session_id="$(generate_session_id)"
  if [[ -z "$session_id" ]]; then
    err "Could not generate a temporary Copilot session ID"
    return 1
  fi
  if [[ -n "$MODEL" ]]; then
    cmd+=(--model "$MODEL")
  fi
  cmd+=(
    --max-ai-credits "$MAX_AI_CREDITS"
    --session-id "$session_id"
    --no-remote-export
    --allow-all
    -s
    --no-ask-user
  )

  local exit_code=0
  output=$("${cmd[@]}" 2>&1) || exit_code=$?

  local cleanup_exit_code=0
  delete_copilot_session "$session_id" || cleanup_exit_code=$?
  if [[ $cleanup_exit_code -ne 0 ]]; then
    err "Failed to delete completed Copilot session $session_id"
    echo "$output"
    return "$cleanup_exit_code"
  fi

  if [[ $exit_code -ne 0 ]]; then
    err "Copilot exited with code $exit_code"
    echo "$output"
    return $exit_code
  fi

  echo "$output"
  return 0
}

# Human-readable preview of the Copilot invocation (for --print-only logging).
# Avoids dumping the full prompt; shows the flag shape only.
agent_cmdline_preview() {
  local model_preview=""
  if [[ -n "$MODEL" ]]; then
    model_preview=" --model $MODEL"
  fi

  echo "copilot -p <prompt>${model_preview} --max-ai-credits $MAX_AI_CREDITS --session-id <temporary-uuid> --no-remote-export --allow-all -s --no-ask-user"
}

# ── Self-correction agent call ───────────────────────────────────────────────
# When --selfcorrect is active, this function is called after a task failure
# (but before the retry). It gives an agent the failure output and asks it to
# diagnose and fix the root cause in the codebase or task file.
#
# Arguments:
#   $1 — task_desc (the failing task description)
#   $2 — parent_text (parent task context, may be empty)
#   $3 — status_type (PARTIAL|COMPLETE|"" for crash/no-status)
#   $4 — status_detail (detail from TASK_STATUS line, may be empty)
#   $5 — task_output (full output from the failing agent call)
#   $6 — attempt_number (which retry attempt triggered this)
#
# Returns 0 on success, non-zero on failure (caller should ignore failures).

run_selfcorrect() {
  local task_desc="$1"
  local parent_text="$2"
  local status_type="$3"
  local status_detail="$4"
  local task_output="$5"
  local attempt_num="$6"

  log "Self-correcting: analyzing failure and attempting fix..."

  local failure_context=""
  if [[ -n "$status_type" ]]; then
    failure_context="The agent reported status: $status_type — $status_detail"
  else
    failure_context="The agent crashed or produced no parseable TASK_STATUS line."
  fi

  local selfcorrect_prompt="You are a self-correction agent. A task execution just failed, and your job is to analyze the failure and fix the root cause so the task can succeed on retry.

## Context

Task file: @$TASKS_FILE — read it for full context.
Failed task: $task_desc
${parent_text:+Parent task: $parent_text}

## What went wrong

$failure_context

## Output from the failing attempt

\`\`\`
$task_output
\`\`\`

## Your Instructions

1. Analyze the failure output above to identify the root cause.
2. Determine if the failure can be fixed by modifying code, configuration, or the task file itself.
3. If fixable: make the necessary changes (edit code, fix config, update the task file with better instructions, etc.)
4. If NOT fixable by a coding agent (requires human intervention, external access, etc.): do nothing — just explain why.
5. Do NOT mark the task as complete — the retry will handle that.
6. Do NOT attempt to re-run the original task — just fix the underlying issue.

As the VERY LAST line of your response, output exactly one of:
  SELFCORRECT_RESULT: FIXED — <what you changed>
  SELFCORRECT_RESULT: UNFIXABLE — <why it cannot be fixed by a coding agent>
  SELFCORRECT_RESULT: ATTEMPTED — <what you tried but are unsure if it will work>"

  local selfcorrect_log="$LOG_DIR/task_$(printf '%02d' $task_number)_selfcorrect_attempt_${attempt_num}.log"

  local sc_output
  if sc_output=$(run_agent "$selfcorrect_prompt"); then
    echo "$sc_output" > "$selfcorrect_log"
    log "Self-correct output saved to: $selfcorrect_log"
    echo "selfcorrect ($task_desc, attempt $attempt_num) -> $selfcorrect_log" >> "$LOGS_INDEX"

    # Parse the result for logging
    local sc_result_line
    sc_result_line=$(echo "$sc_output" | grep -i "^SELFCORRECT_RESULT:" | tail -1 || true)
    if [[ -n "$sc_result_line" ]]; then
      ok "Self-correct: $sc_result_line"
    else
      warn "Self-correct: no SELFCORRECT_RESULT line found (check log)"
    fi
    return 0
  else
    echo "$sc_output" > "$selfcorrect_log" 2>/dev/null || true
    warn "Self-correct call failed (non-fatal) — log: $selfcorrect_log"
    echo "selfcorrect ($task_desc, attempt $attempt_num) -> $selfcorrect_log" >> "$LOGS_INDEX"
    return 1
  fi
}

# ── Main loop ────────────────────────────────────────────────────────────────

header "Ralph Wiggum Loop"
log "Tasks file: $TASKS_FILE"
log "Agent: Copilot | Model: ${MODEL:-default} | Task scope: $TASK_SCOPE | Max retries: $MAX_RETRIES | Max AI credits/call: $MAX_AI_CREDITS | Self-correct: $SELFCORRECT | Verbose: $VERBOSE"
if [[ -n "$SYSTEM_PROMPT_FILE" ]]; then
  log "System prompt: $SYSTEM_PROMPT_FILE"
fi

# Set up per-task log directory (at repo root so it survives folder moves during feature completion)
REPO_ROOT="$(git -C "$TASKS_DIR" rev-parse --show-toplevel 2>/dev/null || echo "$TASKS_DIR")"
LOG_DIR="$REPO_ROOT/.ralph-logs/$(basename "$TASKS_DIR")/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"
LOGS_INDEX="$TASKS_DIR/ralph-logs-index.md"
if ! $PRINT_ONLY; then
  log "Task logs: $LOG_DIR"
  log "Logs index: $LOGS_INDEX"
fi

echo ""

task_number=0
any_failures=false

while true; do
  # Re-read file each iteration because the agent may have modified it.
  if [[ "$TASK_SCOPE" == "parent" ]]; then
    verbose "Main loop: calling get_next_parent"
    task_info=$(get_next_parent) && next_exit=0 || next_exit=$?
  else
    verbose "Main loop: calling get_next_task"
    task_info=$(get_next_task) && next_exit=0 || next_exit=$?
  fi

  if [[ $next_exit -eq 1 ]]; then
    log "No more incomplete tasks — exiting loop"
    break
  elif [[ $next_exit -ne 0 ]]; then
    err "Failed to get next $TASK_SCOPE task (exit $next_exit) — aborting task loop"
    # Snapshot task file state for post-mortem debugging
    if [[ -d "$LOG_DIR" ]]; then
      cp "$TASKS_FILE" "$LOG_DIR/tasks_at_failure.md" 2>/dev/null || true
      err "Task file snapshot saved to: $LOG_DIR/tasks_at_failure.md"
    fi
    any_failures=true
    break
  fi

  line_num=""
  parent_end_line=""
  parent_text=""
  parent_task_context=""

  if [[ "$TASK_SCOPE" == "parent" ]]; then
    task_metadata="${task_info%%$'\n'*}"
    parent_task_context="${task_info#*$'\n'}"
    line_num="${task_metadata%%|*}"
    rest="${task_metadata#*|}"
    parent_end_line="${rest%%|*}"
    task_desc="${rest#*|}"
  else
    line_num="${task_info%%|*}"
    rest="${task_info#*|}"
    parent_text="${rest%%|*}"
    task_desc="${rest#*|}"
  fi
  task_number=$((task_number + 1))

  verbose "Main loop: calling count_tasks"
  counts="$(count_tasks)" && count_exit=0 || count_exit=$?
  if [[ $count_exit -ne 0 ]]; then
    warn "count_tasks failed (exit $count_exit) — using fallback counts"
    counts="?|?|?"
  fi
  total="${counts%%|*}"
  rest2="${counts#*|}"
  completed="${rest2%%|*}"

  if [[ "$TASK_SCOPE" == "parent" ]]; then
    header "Parent task $task_number: $task_desc"
  else
    header "Task $task_number of $total: $task_desc"
  fi
  if [[ -n "$parent_text" ]]; then
    log "Parent: $parent_text"
  fi

  retries=0
  task_succeeded=false
  retry_context=""

  while [[ $retries -le $MAX_RETRIES ]]; do
    if [[ $retries -gt 0 ]]; then
      warn "Retry $retries/$MAX_RETRIES for: $task_desc"
    fi

    if [[ "$TASK_SCOPE" == "parent" ]]; then
      prompt="You are working through a task list. The full plan is in @$TASKS_FILE — read it for broader context.

Your job right now is to implement the entire following parent task, including every incomplete nested subtask:

---
$parent_task_context
---
$retry_context

Instructions:
1. Before starting, evaluate whether this parent task is something you can actually perform as a software engineering agent. You can write code, edit files, run commands, search codebases, and interact with development tools. You CANNOT perform physical actions, interact with the real world, access external accounts/services you have no credentials for, or do anything outside your capabilities as a coding assistant. If the parent task is not something you can perform, report BLOCKED immediately — do NOT pretend to complete it or mark it [x].
2. Implement all incomplete subtasks in this parent task completely. Preserve already completed subtasks.
3. Follow the task list's TDD ordering and any parent-level notes or acceptance criteria.
4. As each subtask is completed, change its \`[ ]\` marker to \`[x]\` in the task file.
5. When every subtask under this parent is resolved, mark the parent task \`[x]\`.
6. Update the \"Relevant Files\" section in the task file with any files you created or modified.
7. Do NOT work on any other parent tasks.

IMPORTANT — as the VERY LAST line of your response, output exactly ONE of these status lines:
  TASK_STATUS: COMPLETE — <one-line summary of the completed parent task>
  TASK_STATUS: BLOCKED — <reason why you cannot complete this parent task>
  TASK_STATUS: PARTIAL — <what you completed and which subtasks remain>
Use BLOCKED for tasks that are impossible, nonsensical, or outside your capabilities as a software agent. Do not fabricate success.
This status line is machine-parsed. Do not omit it."
    else
      # Build context about the parent and sibling tasks.
      parent_context=""
      if [[ -n "$parent_text" ]]; then
        parent_context="
This subtask belongs to the parent task group:
  $parent_text
"
      fi

      prompt="You are working through a task list. The full plan is in @$TASKS_FILE — read it for context.
$parent_context
Your job right now is to implement ONLY the following subtask:

  $task_desc
$retry_context

Instructions:
1. Before starting, evaluate whether this task is something you can actually perform as a software engineering agent. You can write code, edit files, run commands, search codebases, and interact with development tools. You CANNOT perform physical actions, interact with the real world, access external accounts/services you have no credentials for, or do anything outside your capabilities as a coding assistant. If the task is not something you can perform, report BLOCKED immediately — do NOT pretend to complete it or mark it [x].
2. Implement this subtask completely.
3. When done, mark it as completed by changing \`[ ]\` to \`[x]\` in the task file.
4. If ALL sibling subtasks under the parent are now \`[x]\`, also mark the parent task \`[x]\`.
5. Update the \"Relevant Files\" section in the task file with any files you created or modified.
6. Do NOT work on any other tasks. Focus exclusively on this one.

IMPORTANT — as the VERY LAST line of your response, output exactly ONE of these status lines:
  TASK_STATUS: COMPLETE — <one-line summary of what you did>
  TASK_STATUS: BLOCKED — <reason why you cannot complete this task>
  TASK_STATUS: PARTIAL — <what you did and what remains>
Use BLOCKED for tasks that are impossible, nonsensical, or outside your capabilities as a software agent. Do not fabricate success.
This status line is machine-parsed. Do not omit it."
    fi

    log "Calling Copilot..."
    task_log="$LOG_DIR/task_$(printf '%02d' $task_number)_$(echo "$task_desc" | tr -c '[:alnum:]-' '_' | cut -c1-60).log"

    if output=$(run_agent "$prompt"); then
      # Save full output to log file
      echo "$output" > "$task_log"
      log "Full output saved to: $task_log"
      echo "$task_desc -> $task_log" >> "$LOGS_INDEX"

      # Parse the TASK_STATUS line from Copilot's output
      status_line=$(echo "$output" | grep -i "^TASK_STATUS:" | tail -1 || true)

      status_type=""
      status_detail=""
      if [[ "$status_line" =~ ^TASK_STATUS:[[:space:]]*(COMPLETE|BLOCKED|PARTIAL)[[:space:]]*—[[:space:]]*(.*) ]]; then
        status_type="${BASH_REMATCH[1]}"
        status_detail="${BASH_REMATCH[2]}"
      elif [[ "$status_line" =~ ^TASK_STATUS:[[:space:]]*(COMPLETE|BLOCKED|PARTIAL)[[:space:]]*-[[:space:]]*(.*) ]]; then
        # Also accept plain hyphen
        status_type="${BASH_REMATCH[1]}"
        status_detail="${BASH_REMATCH[2]}"
      fi

      if [[ "$TASK_SCOPE" == "parent" ]]; then
        verbose "Running auto_complete_parents before checking parent status"
        if ! auto_complete_parents; then
          warn "auto_complete_parents failed — parent task may need manual completion"
        fi
      fi

      # Check the file for the [x] mark as ground truth.
      file_marked=false
      verbose "Checking if task is marked complete (line $line_num)"
      if $PRINT_ONLY; then
        file_marked=true
      elif [[ "$TASK_SCOPE" == "parent" ]]; then
        if is_parent_task_resolved "$line_num" "$task_desc"; then
          file_marked=true
        fi
      elif is_task_marked_complete "$line_num" "$task_desc"; then
        file_marked=true
      fi
      verbose "file_marked=$file_marked"

      # Decision matrix: status signal + file mark
      if $file_marked; then
        # Task was marked complete in the file — trust it
        ok "Task complete: ${status_detail:-done}"
        echo "$output" | { grep -v "^TASK_STATUS:" || true; } | tail -10
        task_succeeded=true

        if $PRINT_ONLY; then
          if [[ "$TASK_SCOPE" == "parent" ]]; then
            sed -i "${line_num},${parent_end_line}s/- \[ \?\]/- [x]/" "$TASKS_FILE"
          else
            sed -i "${line_num}s/- \[ \?\]/- [x]/" "$TASKS_FILE"
          fi
        fi

        verbose "Running auto_complete_parents"
        if ! auto_complete_parents; then
          warn "auto_complete_parents failed — parent tasks may need manual completion"
        fi
        break

      elif [[ "$status_type" == "BLOCKED" ]]; then
        # Copilot explicitly says it's blocked — mark failed and continue to next task
        err "Task blocked: $status_detail"
        echo "$output" | { grep -v "^TASK_STATUS:" || true; } | tail -10
        if ! mark_work_item_failed "$line_num" "$task_desc"; then
          warn "Failed to mark task failure in the task file"
        fi
        any_failures=true
        break

      elif [[ "$status_type" == "PARTIAL" ]]; then
        # Copilot made progress but didn't finish — retry with context
        warn "Task partial: $status_detail"
        echo "$output" | { grep -v "^TASK_STATUS:" || true; } | tail -10
        retries=$((retries + 1))
        if [[ $retries -gt $MAX_RETRIES ]]; then
          err "Task still incomplete after $MAX_RETRIES retries: $task_desc"
          err "Last status: $status_detail"
          if ! mark_work_item_failed "$line_num" "$task_desc"; then
            warn "Failed to mark task failure in the task file"
          fi
          any_failures=true
          break
        fi
        # Self-correct before retry
        if $SELFCORRECT; then
          run_selfcorrect "$task_desc" "$parent_text" "$status_type" "$status_detail" "$output" "$retries" || true
        fi
        retry_context="
Previous attempt reported: PARTIAL — $status_detail
Please pick up where the previous attempt left off."

      elif [[ "$status_type" == "COMPLETE" ]] && ! $file_marked; then
        # Copilot claims complete but didn't mark the file
        warn "Copilot reported COMPLETE but task not marked [x] in file"
        echo "$output" | { grep -v "^TASK_STATUS:" || true; } | tail -10
        retries=$((retries + 1))
        if [[ $retries -gt $MAX_RETRIES ]]; then
          err "Task reported complete but never marked in file: $task_desc"
          if ! mark_work_item_failed "$line_num" "$task_desc"; then
            warn "Failed to mark task failure in the task file"
          fi
          any_failures=true
          break
        fi
        # Self-correct before retry
        if $SELFCORRECT; then
          run_selfcorrect "$task_desc" "$parent_text" "$status_type" "$status_detail" "$output" "$retries" || true
        fi
        retry_context="
The previous attempt reported COMPLETE, but the task file still contains incomplete work.
Finish the remaining work and update every applicable checkbox."

      else
        # No status line or unrecognized — fall back to file check
        warn "No TASK_STATUS line found in output (check log: $task_log)"
        echo "$output" | { grep -v "^TASK_STATUS:" || true; } | tail -10
        retries=$((retries + 1))
        if [[ $retries -gt $MAX_RETRIES ]]; then
          err "Task did not produce status after $MAX_RETRIES retries: $task_desc"
          if ! mark_work_item_failed "$line_num" "$task_desc"; then
            warn "Failed to mark task failure in the task file"
          fi
          any_failures=true
          break
        fi
        # Self-correct before retry
        if $SELFCORRECT; then
          run_selfcorrect "$task_desc" "$parent_text" "" "" "$output" "$retries" || true
        fi
        retry_context="
The previous attempt did not provide a valid TASK_STATUS line.
Complete the assigned work and include the required status line."
      fi

    else
      # Agent process crashed (non-zero exit)
      exit_code=$?
      echo "$output" > "$task_log" 2>/dev/null || true
      err "Copilot crashed (exit $exit_code) — log: $task_log"
      echo "$task_desc -> $task_log" >> "$LOGS_INDEX"
      echo "$output" | tail -10
      retries=$((retries + 1))
      if [[ $retries -gt $MAX_RETRIES ]]; then
        err "Copilot crashed $MAX_RETRIES times on: $task_desc"
        if ! mark_work_item_failed "$line_num" "$task_desc"; then
          warn "Failed to mark task failure in the task file"
        fi
        any_failures=true
        break
      fi
      # Self-correct before retry
      if $SELFCORRECT; then
        run_selfcorrect "$task_desc" "$parent_text" "" "" "$output" "$retries" || true
      fi
    fi
  done
done

# ── Summary ──────────────────────────────────────────────────────────────────

counts="$(count_tasks)" && true || counts=""
counts_known=false
if [[ "$counts" =~ ^[0-9]+\|[0-9]+\|[0-9]+$ ]]; then
  counts_known=true
  total="${counts%%|*}"
  rest="${counts#*|}"
  completed="${rest%%|*}"
  failed="${rest#*|}"
else
  total="?"
  completed="?"
  failed="?"
fi

echo ""
header "Task Execution Summary"
if ! $counts_known; then
  log "Total: unknown (count_tasks failed) | Failures occurred: $any_failures"
else
  log "Total: $total | Completed: $completed | Failed: $failed | Remaining: $((total - completed - failed))"
fi
if ! $PRINT_ONLY; then
  log "Logs: $LOG_DIR"
fi

if $any_failures; then
  warn "Some tasks failed or were blocked (see above)"
fi

# ── Verification ─────────────────────────────────────────────────────────────
# Always attempt verification after all tasks have been processed,
# regardless of whether some tasks failed or were blocked.

verification="$(get_verification_section)" && true || {
  warn "get_verification_section failed — skipping verification"
  verification=""
}

if [[ -z "${verification// /}" ]]; then
  warn "No verification section found in tasks file — skipping verification"
  if ! $counts_known; then
    err "RESULT: INCOMPLETE — task counts and verification criteria could not be read"
    exit 1
  elif [[ $failed -gt 0 ]] || $any_failures; then
    err "RESULT: INCOMPLETE — $completed/$total tasks completed, $failed failed (no verification criteria defined)"
    exit 1
  else
    ok "RESULT: ALL $total TASKS COMPLETED (no verification criteria defined)"
    exit 0
  fi
fi

header "Verification"
log "Running verification criteria..."
if $any_failures; then
  log "Note: $failed task(s) failed — running verification on completed work"
fi

verify_prompt="You just completed a set of implementation tasks. Now verify the work.

The full task plan with completion status is in @$TASKS_FILE — read it for context.
Task summary: $completed of $total tasks completed, $failed failed.

Here are the verification criteria:
---
$verification
---

Run the verification checks described above. For each criterion:
1. Actually run the commands or checks needed to verify
2. Report PASS or FAIL for each criterion
3. If any criterion fails, explain exactly what went wrong

At the end, output a single summary line in this exact format:
VERIFICATION_RESULT: PASS
or
VERIFICATION_RESULT: FAIL - <reason>"

if $PRINT_ONLY; then
  run_agent "$verify_prompt"
  ok "RESULT: DRY RUN COMPLETE — verification would run here"
  exit 0
fi

mkdir -p "$LOG_DIR"
verify_log="$LOG_DIR/verification.log"

if verify_output=$(run_agent "$verify_prompt"); then
  echo "$verify_output" > "$verify_log"
  log "Verification log saved to: $verify_log"
  echo "verification -> $verify_log" >> "$LOGS_INDEX"
  echo "$verify_output" | tail -30
  echo ""

  # Extract the result line
  result_line=$(echo "$verify_output" | grep -i "VERIFICATION_RESULT:" | tail -1 || true)

  if [[ "$result_line" == *"PASS"* && "$result_line" != *"FAIL"* ]]; then
    if $any_failures; then
      warn "RESULT: VERIFICATION PASSED but $failed task(s) failed — review failures above"
      warn "Skipping feature completion due to task failures"
      exit 1
    else
      ok "RESULT: ALL $total TASKS COMPLETED — VERIFICATION PASSED"

      # ── Feature Completion ──────────────────────────────────────────────
      header "Feature Completion"
      log "Running complete-feature prompt..."

      complete_prompt_file=""
      repo_root="$(git -C "$TASKS_DIR" rev-parse --show-toplevel 2>/dev/null || echo "")"
      if [[ -n "$repo_root" ]]; then
        candidate="$repo_root/task-helpers/complete-feature.prompt.md"
        if [[ -f "$candidate" ]]; then
          complete_prompt_file="$candidate"
        fi
      fi

      if [[ -z "$complete_prompt_file" ]]; then
        warn "complete-feature.prompt.md not found — skipping feature completion"
        exit 0
      fi

      # Read the prompt file content (strip YAML frontmatter)
      complete_prompt_content=$(awk '
        BEGIN { in_frontmatter=0; found_end=0 }
        NR==1 && /^---[[:space:]]*$/ { in_frontmatter=1; next }
        in_frontmatter && /^---[[:space:]]*$/ { in_frontmatter=0; found_end=1; next }
        !in_frontmatter && found_end { print }
        !in_frontmatter && !found_end && NR>1 { print }
      ' "$complete_prompt_file")

      complete_run_prompt="You have just completed and verified all tasks in @$TASKS_FILE.

Now run the feature completion steps described below:
---
$complete_prompt_content
---

The task file is: $TASKS_FILE
Apply these steps to the feature folder containing that task file."

      verbose "Feature completion: TASKS_DIR=$TASKS_DIR"
      verbose "Feature completion: TASKS_FILE=$TASKS_FILE"
      verbose "Feature completion: LOG_DIR=$LOG_DIR"
      verbose "Feature completion: prompt length=${#complete_run_prompt} chars"

      if $PRINT_ONLY; then
        run_agent "$complete_run_prompt"
        ok "RESULT: DRY RUN COMPLETE — feature completion would run here"
        exit 0
      fi

      mkdir -p "$LOG_DIR"
      complete_log="$LOG_DIR/complete-feature.log"

      if complete_output=$(run_agent "$complete_run_prompt"); then
        # Re-create LOG_DIR in case feature completion moved the parent folder
        if [[ ! -d "$LOG_DIR" ]]; then
          warn "LOG_DIR disappeared during feature completion (folder likely moved) — recreating"
          verbose "Feature completion: original LOG_DIR=$LOG_DIR no longer exists"
          mkdir -p "$LOG_DIR"
        fi
        echo "$complete_output" > "$complete_log"
        log "Feature completion log saved to: $complete_log"
        echo "feature-completion -> $complete_log" >> "$LOGS_INDEX"
        echo "$complete_output" | tail -20
        ok "Feature completion finished"
      else
        # Re-create LOG_DIR defensively before writing error log
        if [[ ! -d "$LOG_DIR" ]]; then
          mkdir -p "$LOG_DIR" 2>/dev/null || true
        fi
        echo "$complete_output" > "$complete_log" 2>/dev/null || true
        warn "Feature completion call failed (log: $complete_log)"
        echo "feature-completion (failed) -> $complete_log" >> "$LOGS_INDEX"
        warn "All tasks passed verification — feature completion can be run manually"
      fi

      exit 0
    fi
  elif [[ -n "$result_line" ]]; then
    err "RESULT: VERIFICATION FAILED ($completed/$total tasks completed)"
    err "$result_line"
    exit 1
  else
    warn "RESULT: Could not parse verification result from Copilot output"
    warn "Review the output above manually (log: $verify_log)"
    exit 2
  fi
else
  err "RESULT: Verification call to Copilot failed (log: $verify_log)"
  exit 1
fi
