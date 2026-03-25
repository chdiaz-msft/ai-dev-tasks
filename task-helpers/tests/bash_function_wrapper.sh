#!/usr/bin/env bash
# Wrapper script to call bash functions from ralph-wiggum.sh
# Usage: bash_function_wrapper.sh <function_name> <task_file>

set -euo pipefail

FUNCTION_NAME="$1"
TASK_FILE="$2"

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RALPH_V1="$REPO_ROOT/ralph-wiggum.sh"

# Extract function definitions from ralph-wiggum.sh
# Functions are between "# ── Task parsing" and "# ── Run Claude headless"
# Strip carriage returns in case ralph-wiggum.sh has CRLF line endings
FUNCTIONS=$(sed -n '/# ── Task parsing/,/# ── Run Claude headless/p' "$RALPH_V1" | head -n -1 | tr -d '\r')

# Load the functions
eval "$FUNCTIONS"

# Set TASKS_FILE for the functions to use
TASKS_FILE="$TASK_FILE"

# Call the requested function
"$FUNCTION_NAME"
