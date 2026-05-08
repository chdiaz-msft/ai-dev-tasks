#!/usr/bin/env bash
set -euo pipefail

# ralph-wiggum.sh — Sequential task executor using Claude Code headless mode
#
# Usage: ./ralph-wiggum.sh <tasks.md> [options]
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
#   --model <model>              Claude model to use (default: sonnet)
#   --max-retries <n>            Max retries per task before giving up (default: 2)
#   --max-budget-usd <n>         Max budget per Claude call in USD (default: 5)
#   --permission-mode <mode>     Permission mode for Claude (default: auto)
#   --system-prompt-file <path>  Prompt file to append to each Claude call
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

MODEL="sonnet"
MAX_RETRIES=2
MAX_BUDGET_USD=5
PERMISSION_MODE="bypassPermissions"
PRINT_ONLY=false
VERBOSE=false
SYSTEM_PROMPT_FILE=""

# ── Arg parsing ──────────────────────────────────────────────────────────────

TASKS_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)              MODEL="$2"; shift 2 ;;
    --max-retries)        MAX_RETRIES="$2"; shift 2 ;;
    --max-budget-usd)     MAX_BUDGET_USD="$2"; shift 2 ;;
    --permission-mode)    PERMISSION_MODE="$2"; shift 2 ;;
    --system-prompt-file) SYSTEM_PROMPT_FILE="$2"; shift 2 ;;
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
  SYSTEM_PROMPT_CONTENT="$SYSTEM_PROMPT_CONTENT

## Headless Mode Override
You are running in HEADLESS/AUTONOMOUS mode via the ralph-wiggum task runner.
- Do NOT wait for user approval between subtasks.
- Do NOT ask the user for permission — there is no interactive user.
- Complete ONLY the assigned subtask, mark it [x] in the task file, and stop.
- If all subtasks under a parent are now [x], also mark the parent [x].
- Update the Relevant Files section if you create or modify files."
fi

# ── Task parsing via Python CLI ─────────────────────────────────────────────

# Get path to the Python CLI task parser (relative to this script)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASK_PARSER="$SCRIPT_DIR/task_parser.py"

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

  verbose "run_parser: uv run task_parser.py $subcmd $*"

  # Capture stdout and stderr separately; prevent set -e from killing us
  stdout_output=$(uv run "$TASK_PARSER" "$subcmd" "$@" 2>"$stderr_file") && exit_code=0 || exit_code=$?
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

# Mark a task as failed by line number and description (for stable addressing)
mark_task_failed() {
  local line_num="$1"
  local task_desc="$2"
  run_parser mark-failed "$TASKS_FILE" --line "$line_num" --match "$task_desc"
}

# Check if a specific line is now marked complete [x]
is_task_marked_complete() {
  local line_num="$1"
  local task_desc="$2"
  run_parser is-complete "$TASKS_FILE" --line "$line_num" --match "$task_desc"
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

# ── Run Claude headless ──────────────────────────────────────────────────────

run_claude() {
  local prompt="$1"
  local output

  if $PRINT_ONLY; then
    log "(dry run) Would send to Claude:"
    echo ""
    echo "$prompt"
    echo ""
    if [[ -n "$SYSTEM_PROMPT_CONTENT" ]]; then
      log "(dry run) With appended system prompt (${#SYSTEM_PROMPT_CONTENT} chars)"
    fi
    return 0
  fi

  local -a claude_args=(
    -p
    --model "$MODEL"
    --permission-mode "$PERMISSION_MODE"
    --max-budget-usd "$MAX_BUDGET_USD"
  )

  if [[ -n "$SYSTEM_PROMPT_CONTENT" ]]; then
    claude_args+=(--append-system-prompt "$SYSTEM_PROMPT_CONTENT")
  fi

  claude_args+=("$prompt")

  output=$(claude "${claude_args[@]}" 2>&1) || {
    local exit_code=$?
    err "Claude exited with code $exit_code"
    echo "$output"
    return $exit_code
  }

  echo "$output"
  return 0
}

# ── Main loop ────────────────────────────────────────────────────────────────

header "Ralph Wiggum Loop"
log "Tasks file: $TASKS_FILE"
log "Model: $MODEL | Max retries: $MAX_RETRIES | Budget/call: \$$MAX_BUDGET_USD | Verbose: $VERBOSE"
if [[ -n "$SYSTEM_PROMPT_FILE" ]]; then
  log "System prompt: $SYSTEM_PROMPT_FILE"
fi

# Set up per-task log directory (at repo root so it survives folder moves during feature completion)
REPO_ROOT="$(git -C "$TASKS_DIR" rev-parse --show-toplevel 2>/dev/null || echo "$TASKS_DIR")"
LOG_DIR="$REPO_ROOT/.ralph-logs/$(basename "$TASKS_DIR")/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"
if ! $PRINT_ONLY; then
  log "Task logs: $LOG_DIR"
fi

echo ""

task_number=0
any_failures=false

while true; do
  # Re-read file each iteration (Claude may have modified it)
  # Get next incomplete leaf task
  verbose "Main loop: calling get_next_task"
  task_info=$(get_next_task) && next_exit=0 || next_exit=$?

  if [[ $next_exit -eq 1 ]]; then
    log "No more incomplete tasks — exiting loop"
    break
  elif [[ $next_exit -ne 0 ]]; then
    err "get_next_task failed (exit $next_exit) — aborting task loop"
    # Snapshot task file state for post-mortem debugging
    if [[ -d "$LOG_DIR" ]]; then
      cp "$TASKS_FILE" "$LOG_DIR/tasks_at_failure.md" 2>/dev/null || true
      err "Task file snapshot saved to: $LOG_DIR/tasks_at_failure.md"
    fi
    any_failures=true
    break
  fi

  verbose "Main loop: get_next_task returned: $task_info"

  line_num="${task_info%%|*}"
  rest="${task_info#*|}"
  parent_text="${rest%%|*}"
  task_desc="${rest#*|}"
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

  header "Task $task_number of $total: $task_desc"
  if [[ -n "$parent_text" ]]; then
    log "Parent: $parent_text"
  fi

  retries=0
  task_succeeded=false

  while [[ $retries -le $MAX_RETRIES ]]; do
    if [[ $retries -gt 0 ]]; then
      warn "Retry $retries/$MAX_RETRIES for: $task_desc"
    fi

    # Build context about the parent and sibling tasks
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

    log "Calling Claude..."
    task_log="$LOG_DIR/task_$(printf '%02d' $task_number)_$(echo "$task_desc" | tr -c '[:alnum:]-' '_' | cut -c1-60).log"

    if output=$(run_claude "$prompt"); then
      # Save full output to log file
      echo "$output" > "$task_log"
      log "Full output saved to: $task_log"

      # Parse the TASK_STATUS line from Claude's output
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

      # Check the file for the [x] mark as ground truth
      file_marked=false
      verbose "Checking if task is marked complete (line $line_num)"
      if $PRINT_ONLY || is_task_marked_complete "$line_num" "$task_desc"; then
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
          sed -i "${line_num}s/- \[ \?\]/- [x]/" "$TASKS_FILE"
        fi

        verbose "Running auto_complete_parents"
        if ! auto_complete_parents; then
          warn "auto_complete_parents failed — parent tasks may need manual completion"
        fi
        break

      elif [[ "$status_type" == "BLOCKED" ]]; then
        # Claude explicitly says it's blocked — mark failed and continue to next task
        err "Task blocked: $status_detail"
        echo "$output" | { grep -v "^TASK_STATUS:" || true; } | tail -10
        if ! mark_task_failed "$line_num" "$task_desc"; then
          warn "mark_task_failed also failed — task may not be marked [!] in file"
        fi
        any_failures=true
        break

      elif [[ "$status_type" == "PARTIAL" ]]; then
        # Claude made progress but didn't finish — retry with context
        warn "Task partial: $status_detail"
        echo "$output" | { grep -v "^TASK_STATUS:" || true; } | tail -10
        retries=$((retries + 1))
        if [[ $retries -gt $MAX_RETRIES ]]; then
          err "Task still incomplete after $MAX_RETRIES retries: $task_desc"
          err "Last status: $status_detail"
          if ! mark_task_failed "$line_num" "$task_desc"; then
          warn "mark_task_failed also failed — task may not be marked [!] in file"
        fi
          any_failures=true
          break
        fi
        # Add failure context to next retry prompt
        parent_context="$parent_context
Previous attempt reported: PARTIAL — $status_detail
Please pick up where the previous attempt left off."

      elif [[ "$status_type" == "COMPLETE" ]] && ! $file_marked; then
        # Claude claims complete but didn't mark the file
        warn "Claude reported COMPLETE but task not marked [x] in file"
        echo "$output" | { grep -v "^TASK_STATUS:" || true; } | tail -10
        retries=$((retries + 1))
        if [[ $retries -gt $MAX_RETRIES ]]; then
          err "Task reported complete but never marked in file: $task_desc"
          if ! mark_task_failed "$line_num" "$task_desc"; then
          warn "mark_task_failed also failed — task may not be marked [!] in file"
        fi
          any_failures=true
          break
        fi

      else
        # No status line or unrecognized — fall back to file check
        warn "No TASK_STATUS line found in output (check log: $task_log)"
        echo "$output" | { grep -v "^TASK_STATUS:" || true; } | tail -10
        retries=$((retries + 1))
        if [[ $retries -gt $MAX_RETRIES ]]; then
          err "Task did not produce status after $MAX_RETRIES retries: $task_desc"
          if ! mark_task_failed "$line_num" "$task_desc"; then
          warn "mark_task_failed also failed — task may not be marked [!] in file"
        fi
          any_failures=true
          break
        fi
      fi

    else
      # Claude process crashed (non-zero exit)
      exit_code=$?
      echo "$output" > "$task_log" 2>/dev/null || true
      err "Claude crashed (exit $exit_code) — log: $task_log"
      echo "$output" | tail -10
      retries=$((retries + 1))
      if [[ $retries -gt $MAX_RETRIES ]]; then
        err "Claude crashed $MAX_RETRIES times on: $task_desc"
        if ! mark_task_failed "$line_num" "$task_desc"; then
          warn "mark_task_failed also failed — task may not be marked [!] in file"
        fi
        any_failures=true
        break
      fi
    fi
  done
done

# ── Summary ──────────────────────────────────────────────────────────────────

counts="$(count_tasks)" && true || counts="?|?|?"
total="${counts%%|*}"
rest="${counts#*|}"
completed="${rest%%|*}"
failed="${rest#*|}"

echo ""
header "Task Execution Summary"
if [[ "$total" == "?" ]]; then
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
  if [[ $failed -gt 0 ]]; then
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
  run_claude "$verify_prompt"
  ok "RESULT: DRY RUN COMPLETE — verification would run here"
  exit 0
fi

mkdir -p "$LOG_DIR"
verify_log="$LOG_DIR/verification.log"

if verify_output=$(run_claude "$verify_prompt"); then
  echo "$verify_output" > "$verify_log"
  log "Verification log saved to: $verify_log"
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
        run_claude "$complete_run_prompt"
        ok "RESULT: DRY RUN COMPLETE — feature completion would run here"
        exit 0
      fi

      mkdir -p "$LOG_DIR"
      complete_log="$LOG_DIR/complete-feature.log"

      if complete_output=$(run_claude "$complete_run_prompt"); then
        # Re-create LOG_DIR in case feature completion moved the parent folder
        if [[ ! -d "$LOG_DIR" ]]; then
          warn "LOG_DIR disappeared during feature completion (folder likely moved) — recreating"
          verbose "Feature completion: original LOG_DIR=$LOG_DIR no longer exists"
          mkdir -p "$LOG_DIR"
        fi
        echo "$complete_output" > "$complete_log"
        log "Feature completion log saved to: $complete_log"
        echo "$complete_output" | tail -20
        ok "Feature completion finished"
      else
        # Re-create LOG_DIR defensively before writing error log
        if [[ ! -d "$LOG_DIR" ]]; then
          mkdir -p "$LOG_DIR" 2>/dev/null || true
        fi
        echo "$complete_output" > "$complete_log" 2>/dev/null || true
        warn "Feature completion call failed (log: $complete_log)"
        warn "All tasks passed verification — feature completion can be run manually"
      fi

      exit 0
    fi
  elif [[ -n "$result_line" ]]; then
    err "RESULT: VERIFICATION FAILED ($completed/$total tasks completed)"
    err "$result_line"
    exit 1
  else
    warn "RESULT: Could not parse verification result from Claude output"
    warn "Review the output above manually (log: $verify_log)"
    exit 2
  fi
else
  err "RESULT: Verification call to Claude failed (log: $verify_log)"
  exit 1
fi
