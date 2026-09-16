#!/usr/bin/env bash
set -euo pipefail

# Parent-task executor: sends one complete top-level task subtree per agent call.
#
# Usage: ./ralph-wiggum-parent-tasks.sh <tasks.md> [ralph-wiggum-v2 options]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RALPH_TASK_SCOPE=parent exec "$SCRIPT_DIR/ralph-wiggum-v2.sh" "$@"
