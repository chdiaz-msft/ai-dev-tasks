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

# ── Colors & output helpers ──────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

log()   { echo -e "${BLUE}[ralph]${NC} $*"; }
ok()    { echo -e "${GREEN}[ralph]${NC} $*"; }
warn()  { echo -e "${YELLOW}[ralph]${NC} $*"; }
err()   { echo -e "${RED}[ralph]${NC} $*" >&2; }
header(){ echo -e "\n${BOLD}${CYAN}═══ $* ═══${NC}\n"; }

# ── Defaults ─────────────────────────────────────────────────────────────────

MODEL="sonnet"
MAX_RETRIES=2
MAX_BUDGET_USD=5
PERMISSION_MODE="bypassPermissions"
PRINT_ONLY=false
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

# ── Task parsing ─────────────────────────────────────────────────────────────

# Get indent level (number of leading spaces) of a line
get_indent() {
  local line="$1"
  local stripped="${line#"${line%%[! ]*}"}"
  echo $(( ${#line} - ${#stripped} ))
}

# Check if a line is a checkbox (any state: [ ], [], [x], [!])
is_checkbox() {
  [[ "$1" =~ ^[[:space:]]*-[[:space:]]\[.\] ]] || [[ "$1" =~ ^[[:space:]]*-[[:space:]]\[\] ]]
}

# Check if a line is an incomplete checkbox ([ ] or [])
is_incomplete() {
  [[ "$1" =~ ^[[:space:]]*-[[:space:]]\[[[:space:]]\] ]] || [[ "$1" =~ ^[[:space:]]*-[[:space:]]\[\] ]]
}

# Check if a line is a completed checkbox
is_complete() {
  [[ "$1" =~ ^[[:space:]]*-[[:space:]]\[x\] ]]
}

# Determine if line at given index has subtasks (next checkbox is more indented)
has_subtasks() {
  local target_indent="$1"
  shift
  local -n lines_ref=$1
  local start_idx=$2
  local total=${#lines_ref[@]}

  local _k
  for (( _k=start_idx+1; _k<total; _k++ )); do
    local line="${lines_ref[$_k]}"
    # Skip blank lines
    [[ -z "${line// /}" ]] && continue
    # If it's a checkbox, check indent
    if is_checkbox "$line"; then
      local child_indent
      child_indent=$(get_indent "$line")
      if (( child_indent > target_indent )); then
        return 0  # has subtasks
      else
        return 1  # next checkbox is at same or lesser indent — no subtasks
      fi
    fi
    # If it's a header, stop
    [[ "$line" =~ ^#{1,6}[[:space:]] ]] && return 1
  done
  return 1
}

# Get the next incomplete LEAF task (skips parents that have subtasks)
# Returns: line_number|parent_text|task_text
#   line_number: 1-based line in the file
#   parent_text: text of the parent task (empty if top-level)
#   task_text:   text of the subtask itself
get_next_task() {
  local in_tasks=false
  local -a file_lines=()
  local line_num=0

  # Read entire file into array
  while IFS= read -r line; do
    file_lines+=("$line")
  done < "$TASKS_FILE"

  local total=${#file_lines[@]}
  local current_parent=""
  local current_parent_indent=-1

  for (( i=0; i<total; i++ )); do
    local line="${file_lines[$i]}"
    line_num=$((i + 1))

    # Detect section headers (h1–h3)
    if [[ "$line" =~ ^#{1,3}[[:space:]]+(.*) ]]; then
      local heading="${BASH_REMATCH[1]}"
      local heading_lower
      heading_lower="$(echo "$heading" | tr '[:upper:]' '[:lower:]')"
      if [[ "$heading_lower" == "tasks" || "$heading_lower" == task* ]]; then
        in_tasks=true
        continue
      elif $in_tasks; then
        # Hit a new section header — tasks section is over
        break
      fi
      continue
    fi

    if ! $in_tasks; then
      continue
    fi

    # Track parent tasks
    if is_checkbox "$line"; then
      local indent
      indent=$(get_indent "$line")

      # If this is a top-level or less-indented checkbox, it could be a parent
      if (( indent <= current_parent_indent )) || (( current_parent_indent == -1 )); then
        if has_subtasks "$indent" file_lines "$i"; then
          # This is a parent — extract its text, track it
          if [[ "$line" =~ ^[[:space:]]*-[[:space:]]\[.?\][[:space:]]+(.*) ]]; then
            current_parent="${BASH_REMATCH[1]}"
            current_parent_indent=$indent
          fi
          continue
        fi
      fi

      # This is a leaf task (or a subtask under a parent)
      if is_incomplete "$line"; then
        local task_text=""
        if [[ "$line" =~ ^[[:space:]]*-[[:space:]]\[[[:space:]]?\][[:space:]]+(.*) ]]; then
          task_text="${BASH_REMATCH[1]}"
        fi
        echo "${line_num}|${current_parent}|${task_text}"
        return 0
      fi
    fi
  done

  return 1  # No incomplete tasks
}

# Mark a task as failed by line number (script-only marker)
mark_task_failed() {
  local line_num="$1"
  sed -i "${line_num}s/- \[ \?\]/- [!]/" "$TASKS_FILE"
}

# Check if a specific line is now marked complete [x]
is_task_marked_complete() {
  local line_num="$1"
  local line
  line=$(sed -n "${line_num}p" "$TASKS_FILE")
  is_complete "$line"
}

# Auto-complete parent tasks whose subtasks are all [x]
auto_complete_parents() {
  local in_tasks=false
  local -a file_lines=()

  while IFS= read -r line; do
    file_lines+=("$line")
  done < "$TASKS_FILE"

  local total=${#file_lines[@]}
  local changed=false

  for (( i=0; i<total; i++ )); do
    local line="${file_lines[$i]}"

    # Detect section headers
    if [[ "$line" =~ ^#{1,3}[[:space:]]+(.*) ]]; then
      local heading="${BASH_REMATCH[1]}"
      local heading_lower
      heading_lower="$(echo "$heading" | tr '[:upper:]' '[:lower:]')"
      if [[ "$heading_lower" == "tasks" || "$heading_lower" == task* ]]; then
        in_tasks=true; continue
      elif $in_tasks; then
        break
      fi
      continue
    fi

    if ! $in_tasks; then continue; fi

    # Only look at incomplete checkboxes that are parents
    if is_incomplete "$line"; then
      local indent
      indent=$(get_indent "$line")

      if has_subtasks "$indent" file_lines "$i"; then
        # Check if ALL subtasks are complete
        local all_complete=true
        for (( j=i+1; j<total; j++ )); do
          local child="${file_lines[$j]}"
          [[ -z "${child// /}" ]] && continue
          if is_checkbox "$child"; then
            local child_indent
            child_indent=$(get_indent "$child")
            if (( child_indent <= indent )); then
              break  # No longer in this parent's subtasks
            fi
            if ! is_complete "$child"; then
              all_complete=false
              break
            fi
          fi
          [[ "$child" =~ ^#{1,6}[[:space:]] ]] && break
        done

        if $all_complete; then
          local real_line=$((i + 1))
          sed -i "${real_line}s/- \[ \?\]/- [x]/" "$TASKS_FILE"
          ok "Auto-completed parent task on line $real_line"
          changed=true
        fi
      fi
    fi
  done

  # If we made changes, recurse in case of multi-level nesting
  if $changed; then
    auto_complete_parents
  fi
}

# Extract the verification section content (supports h1, h2, h3 headers)
get_verification_section() {
  local in_verification=false
  local content=""

  while IFS= read -r line; do
    if [[ "$line" =~ ^#{1,3}[[:space:]]+(.*) ]]; then
      local heading="${BASH_REMATCH[1]}"
      local heading_lower
      heading_lower="$(echo "$heading" | tr '[:upper:]' '[:lower:]')"
      if [[ "$heading_lower" == verification* || "$heading_lower" == verif* ]]; then
        in_verification=true
        continue
      elif $in_verification; then
        break  # Hit next section, stop
      fi
    fi

    if $in_verification; then
      content+="$line"$'\n'
    fi
  done < "$TASKS_FILE"

  echo "$content"
}

# Count total and completed tasks (leaves only)
count_tasks() {
  local total=0
  local completed=0
  local failed=0
  local in_tasks=false
  local -a file_lines=()

  while IFS= read -r line; do
    file_lines+=("$line")
  done < "$TASKS_FILE"

  local num_lines=${#file_lines[@]}

  for (( i=0; i<num_lines; i++ )); do
    local line="${file_lines[$i]}"

    if [[ "$line" =~ ^#{1,3}[[:space:]]+(.*) ]]; then
      local heading="${BASH_REMATCH[1]}"
      local heading_lower
      heading_lower="$(echo "$heading" | tr '[:upper:]' '[:lower:]')"
      if [[ "$heading_lower" == "tasks" || "$heading_lower" == task* ]]; then
        in_tasks=true; continue
      elif $in_tasks; then
        break
      fi
      continue
    fi

    if $in_tasks && is_checkbox "$line"; then
      local indent
      indent=$(get_indent "$line")
      # Only count leaf tasks (no subtasks)
      if ! has_subtasks "$indent" file_lines "$i"; then
        total=$((total + 1))
        if is_complete "$line"; then
          completed=$((completed + 1))
        elif [[ "$line" =~ ^[[:space:]]*-[[:space:]]\[!\] ]]; then
          failed=$((failed + 1))
        fi
      fi
    fi
  done

  echo "${total}|${completed}|${failed}"
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
log "Model: $MODEL | Max retries: $MAX_RETRIES | Budget/call: \$$MAX_BUDGET_USD"
if [[ -n "$SYSTEM_PROMPT_FILE" ]]; then
  log "System prompt: $SYSTEM_PROMPT_FILE"
fi

# Set up per-task log directory
LOG_DIR="$TASKS_DIR/.ralph-logs/$(date +%Y%m%d-%H%M%S)"
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
  task_info=$(get_next_task) || break

  line_num="${task_info%%|*}"
  rest="${task_info#*|}"
  parent_text="${rest%%|*}"
  task_desc="${rest#*|}"
  task_number=$((task_number + 1))

  counts="$(count_tasks)"
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
      if $PRINT_ONLY || is_task_marked_complete "$line_num"; then
        file_marked=true
      fi

      # Decision matrix: status signal + file mark
      if $file_marked; then
        # Task was marked complete in the file — trust it
        ok "Task complete: ${status_detail:-done}"
        echo "$output" | tail -10
        task_succeeded=true

        if $PRINT_ONLY; then
          sed -i "${line_num}s/- \[ \?\]/- [x]/" "$TASKS_FILE"
        fi

        auto_complete_parents
        break

      elif [[ "$status_type" == "BLOCKED" ]]; then
        # Claude explicitly says it's blocked — mark failed and continue to next task
        err "Task blocked: $status_detail"
        echo "$output" | tail -10
        mark_task_failed "$line_num"
        any_failures=true
        break

      elif [[ "$status_type" == "PARTIAL" ]]; then
        # Claude made progress but didn't finish — retry with context
        warn "Task partial: $status_detail"
        echo "$output" | tail -10
        retries=$((retries + 1))
        if [[ $retries -gt $MAX_RETRIES ]]; then
          err "Task still incomplete after $MAX_RETRIES retries: $task_desc"
          err "Last status: $status_detail"
          mark_task_failed "$line_num"
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
        echo "$output" | tail -10
        retries=$((retries + 1))
        if [[ $retries -gt $MAX_RETRIES ]]; then
          err "Task reported complete but never marked in file: $task_desc"
          mark_task_failed "$line_num"
          any_failures=true
          break
        fi

      else
        # No status line or unrecognized — fall back to file check
        warn "No TASK_STATUS line found in output (check log: $task_log)"
        echo "$output" | tail -10
        retries=$((retries + 1))
        if [[ $retries -gt $MAX_RETRIES ]]; then
          err "Task did not produce status after $MAX_RETRIES retries: $task_desc"
          mark_task_failed "$line_num"
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
        mark_task_failed "$line_num"
        any_failures=true
        break
      fi
    fi
  done
done

# ── Summary ──────────────────────────────────────────────────────────────────

counts="$(count_tasks)"
total="${counts%%|*}"
rest="${counts#*|}"
completed="${rest%%|*}"
failed="${rest#*|}"

echo ""
header "Task Execution Summary"
log "Total: $total | Completed: $completed | Failed: $failed | Remaining: $((total - completed - failed))"
if ! $PRINT_ONLY; then
  log "Logs: $LOG_DIR"
fi

if $any_failures; then
  warn "Some tasks failed or were blocked (see above)"
fi

# ── Verification ─────────────────────────────────────────────────────────────
# Always attempt verification after all tasks have been processed,
# regardless of whether some tasks failed or were blocked.

verification="$(get_verification_section)"

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
      exit 1
    else
      ok "RESULT: ALL $total TASKS COMPLETED — VERIFICATION PASSED"
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
