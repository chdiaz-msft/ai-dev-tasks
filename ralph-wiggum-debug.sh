#!/usr/bin/env bash
set -euo pipefail

# ralph-wiggum-debug.sh — Shows the exact prompt sent to Claude headless mode
#
# Usage: ./ralph-wiggum-debug.sh <tasks.md> [options]
#
# Same options as ralph-wiggum.sh but only processes the first task
# and prints the exact inputs instead of calling Claude.

# ── Colors & output helpers ──────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

log()   { echo -e "${BLUE}[debug]${NC} $*"; }
ok()    { echo -e "${GREEN}[debug]${NC} $*"; }
warn()  { echo -e "${YELLOW}[debug]${NC} $*"; }
err()   { echo -e "${RED}[debug]${NC} $*" >&2; }
header(){ echo -e "\n${BOLD}${CYAN}═══ $* ═══${NC}\n"; }
section(){ echo -e "\n${BOLD}── $* ──${NC}\n"; }

# ── Defaults ─────────────────────────────────────────────────────────────────

MODEL="sonnet"
MAX_RETRIES=2
MAX_BUDGET_USD=5
PERMISSION_MODE="bypassPermissions"
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

# ── Auto-discover system prompt file ─────────────────────────────────────────

if [[ -z "$SYSTEM_PROMPT_FILE" ]]; then
  repo_root="$(git -C "$TASKS_DIR" rev-parse --show-toplevel 2>/dev/null || echo "")"
  if [[ -n "$repo_root" ]]; then
    candidate="$repo_root/task-helpers/process-task-list.prompt.md"
    if [[ -f "$candidate" ]]; then
      SYSTEM_PROMPT_FILE="$candidate"
    fi
  fi
fi

# Load and prepare the system prompt content (strip YAML frontmatter)
SYSTEM_PROMPT_CONTENT=""
if [[ -n "$SYSTEM_PROMPT_FILE" && -f "$SYSTEM_PROMPT_FILE" ]]; then
  SYSTEM_PROMPT_CONTENT=$(awk '
    BEGIN { in_frontmatter=0; found_end=0 }
    NR==1 && /^---[[:space:]]*$/ { in_frontmatter=1; next }
    in_frontmatter && /^---[[:space:]]*$/ { in_frontmatter=0; found_end=1; next }
    !in_frontmatter && found_end { print }
    !in_frontmatter && !found_end && NR>1 { print }
  ' "$SYSTEM_PROMPT_FILE")

  SYSTEM_PROMPT_CONTENT="$SYSTEM_PROMPT_CONTENT

## Headless Mode Override
You are running in HEADLESS/AUTONOMOUS mode via the ralph-wiggum task runner.
- Do NOT wait for user approval between subtasks.
- Do NOT ask the user for permission — there is no interactive user.
- Complete ONLY the assigned subtask, mark it [x] in the task file, and stop.
- If all subtasks under a parent are now [x], also mark the parent [x].
- Update the Relevant Files section if you create or modify files."
fi

# ── Task parsing (identical to ralph-wiggum.sh) ─────────────────────────────

get_indent() {
  local line="$1"
  local stripped="${line#"${line%%[! ]*}"}"
  echo $(( ${#line} - ${#stripped} ))
}

is_checkbox() {
  [[ "$1" =~ ^[[:space:]]*-[[:space:]]\[.\] ]] || [[ "$1" =~ ^[[:space:]]*-[[:space:]]\[\] ]]
}

is_incomplete() {
  [[ "$1" =~ ^[[:space:]]*-[[:space:]]\[[[:space:]]\] ]] || [[ "$1" =~ ^[[:space:]]*-[[:space:]]\[\] ]]
}

is_complete() {
  [[ "$1" =~ ^[[:space:]]*-[[:space:]]\[x\] ]]
}

has_subtasks() {
  local target_indent="$1"
  shift
  local -n lines_ref=$1
  local start_idx=$2
  local total=${#lines_ref[@]}

  local _k
  for (( _k=start_idx+1; _k<total; _k++ )); do
    local line="${lines_ref[$_k]}"
    [[ -z "${line// /}" ]] && continue
    if is_checkbox "$line"; then
      local child_indent
      child_indent=$(get_indent "$line")
      if (( child_indent > target_indent )); then
        return 0
      else
        return 1
      fi
    fi
    [[ "$line" =~ ^#{1,6}[[:space:]] ]] && return 1
  done
  return 1
}

get_next_task() {
  local in_tasks=false
  local -a file_lines=()
  local line_num=0

  while IFS= read -r line; do
    file_lines+=("$line")
  done < "$TASKS_FILE"

  local total=${#file_lines[@]}
  local current_parent=""
  local current_parent_indent=-1

  for (( i=0; i<total; i++ )); do
    local line="${file_lines[$i]}"
    line_num=$((i + 1))

    if [[ "$line" =~ ^#{1,3}[[:space:]]+(.*) ]]; then
      local heading="${BASH_REMATCH[1]}"
      local heading_lower
      heading_lower="$(echo "$heading" | tr '[:upper:]' '[:lower:]')"
      if [[ "$heading_lower" == "tasks" ]]; then
        in_tasks=true
        continue
      elif $in_tasks; then
        break
      fi
      continue
    fi

    if ! $in_tasks; then
      continue
    fi

    if is_checkbox "$line"; then
      local indent
      indent=$(get_indent "$line")

      if (( indent <= current_parent_indent )) || (( current_parent_indent == -1 )); then
        if has_subtasks "$indent" file_lines "$i"; then
          if [[ "$line" =~ ^[[:space:]]*-[[:space:]]\[.?\][[:space:]]+(.*) ]]; then
            current_parent="${BASH_REMATCH[1]}"
            current_parent_indent=$indent
          fi
          continue
        fi
      fi

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

  return 1
}

# ── Main: find first task and print everything ───────────────────────────────

header "CLAUDE HEADLESS DEBUG"

log "Tasks file: $TASKS_FILE"
log "Model: $MODEL | Max retries: $MAX_RETRIES | Budget/call: \$$MAX_BUDGET_USD"
if [[ -n "$SYSTEM_PROMPT_FILE" ]]; then
  log "System prompt file: $SYSTEM_PROMPT_FILE"
fi

# Find the first incomplete task
task_info=$(get_next_task) || {
  warn "No incomplete tasks found in: $TASKS_FILE"
  exit 0
}

line_num="${task_info%%|*}"
rest="${task_info#*|}"
parent_text="${rest%%|*}"
task_desc="${rest#*|}"

section "Task Found"
echo "  Line:   $line_num"
echo "  Parent: ${parent_text:-(none)}"
echo "  Task:   $task_desc"

# Build the prompt exactly as ralph-wiggum.sh does
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

# Build the exact CLI command
claude_cmd="claude -p --model \"$MODEL\" --permission-mode \"$PERMISSION_MODE\" --max-budget-usd \"$MAX_BUDGET_USD\""
if [[ -n "$SYSTEM_PROMPT_CONTENT" ]]; then
  claude_cmd="$claude_cmd --append-system-prompt <system-prompt-content>"
fi
claude_cmd="$claude_cmd \"<prompt>\""

section "Claude CLI Command"
echo "  $claude_cmd"

section "Prompt (sent via -p argument)"
echo "$prompt"

if [[ -n "$SYSTEM_PROMPT_CONTENT" ]]; then
  section "Appended System Prompt (--append-system-prompt)"
  echo "$SYSTEM_PROMPT_CONTENT"
else
  section "Appended System Prompt"
  echo "  (none — no system prompt file found)"
fi

echo ""
ok "Done. This is exactly what Claude would receive for this task."
