#!/usr/bin/env bash
# Shell-level integration tests for ralph-wiggum-v2.sh
# Verifies that the v2 script calls Python CLI for all file operations

set -euo pipefail

# Color codes for test output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Test counters
TESTS_RUN=0
TESTS_PASSED=0
TESTS_FAILED=0

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASK_HELPERS_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
RALPH_V2="$TASK_HELPERS_DIR/ralph-wiggum-v2.sh"
TASK_PARSER="$TASK_HELPERS_DIR/task_parser.py"

# Test helpers
pass() {
    echo -e "${GREEN}✓${NC} $1"
    TESTS_PASSED=$((TESTS_PASSED + 1))
}

fail() {
    echo -e "${RED}✗${NC} $1"
    TESTS_FAILED=$((TESTS_FAILED + 1))
}

test_start() {
    TESTS_RUN=$((TESTS_RUN + 1))
    echo -e "\n${YELLOW}TEST $TESTS_RUN:${NC} $1"
}

# Create a test fixture task file
create_test_task_file() {
    local file="$1"
    cat > "$file" << 'EOF'
## Relevant Files
- test.txt

## Tasks

- [ ] 1.0 Parent task
  - [ ] 1.1 First subtask
  - [ ] 1.2 Second subtask
- [ ] 2.0 Another task

## Verification Criteria

- [ ] All tests pass
- [ ] Code is reviewed
EOF
}

# Test 1: Verify ralph-wiggum-v2.sh exists
test_start "ralph-wiggum-v2.sh exists"
if [[ -f "$RALPH_V2" ]]; then
    pass "ralph-wiggum-v2.sh exists at $RALPH_V2"
else
    fail "ralph-wiggum-v2.sh not found at $RALPH_V2"
fi

# Test 2: Verify ralph-wiggum-v2.sh is executable
test_start "ralph-wiggum-v2.sh is executable"
if [[ -x "$RALPH_V2" ]]; then
    pass "ralph-wiggum-v2.sh is executable"
else
    fail "ralph-wiggum-v2.sh is not executable"
fi

# Test 3: Verify ralph-wiggum-v2.sh calls Python CLI for next-task
test_start "v2 script calls Python CLI for next-task operation"
# Create a temporary test file
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TEMP_DIR"' EXIT
TEST_FILE="$TEMP_DIR/test_tasks.md"
create_test_task_file "$TEST_FILE"

# Check if v2 script contains call to task_parser.py next-task
if [[ -f "$RALPH_V2" ]]; then
    if grep -q "task_parser.py.*next-task" "$RALPH_V2" || grep -q "task_parser.py next-task" "$RALPH_V2"; then
        pass "v2 script contains Python CLI call for next-task"
    else
        fail "v2 script does not contain Python CLI call for next-task"
    fi
else
    fail "v2 script not found (skipping test)"
fi

# Test 4: Verify ralph-wiggum-v2.sh calls Python CLI for count
test_start "v2 script calls Python CLI for count operation"
if [[ -f "$RALPH_V2" ]]; then
    if grep -q "task_parser.py.*count" "$RALPH_V2" || grep -q "task_parser.py count" "$RALPH_V2"; then
        pass "v2 script contains Python CLI call for count"
    else
        fail "v2 script does not contain Python CLI call for count"
    fi
else
    fail "v2 script not found (skipping test)"
fi

# Test 5: Verify ralph-wiggum-v2.sh calls Python CLI for is-complete
test_start "v2 script calls Python CLI for is-complete operation"
if [[ -f "$RALPH_V2" ]]; then
    if grep -q "task_parser.py.*is-complete" "$RALPH_V2" || grep -q "task_parser.py is-complete" "$RALPH_V2"; then
        pass "v2 script contains Python CLI call for is-complete"
    else
        fail "v2 script does not contain Python CLI call for is-complete"
    fi
else
    fail "v2 script not found (skipping test)"
fi

# Test 6: Verify ralph-wiggum-v2.sh calls Python CLI for mark-failed
test_start "v2 script calls Python CLI for mark-failed operation"
if [[ -f "$RALPH_V2" ]]; then
    if grep -q "task_parser.py.*mark-failed" "$RALPH_V2" || grep -q "task_parser.py mark-failed" "$RALPH_V2"; then
        pass "v2 script contains Python CLI call for mark-failed"
    else
        fail "v2 script does not contain Python CLI call for mark-failed"
    fi
else
    fail "v2 script not found (skipping test)"
fi

# Test 7: Verify ralph-wiggum-v2.sh calls Python CLI for auto-complete-parents
test_start "v2 script calls Python CLI for auto-complete-parents operation"
if [[ -f "$RALPH_V2" ]]; then
    if grep -q "task_parser.py.*auto-complete-parents" "$RALPH_V2" || grep -q "task_parser.py auto-complete-parents" "$RALPH_V2"; then
        pass "v2 script contains Python CLI call for auto-complete-parents"
    else
        fail "v2 script does not contain Python CLI call for auto-complete-parents"
    fi
else
    fail "v2 script not found (skipping test)"
fi

# Test 8: Verify ralph-wiggum-v2.sh calls Python CLI for verification-section
test_start "v2 script calls Python CLI for verification-section operation"
if [[ -f "$RALPH_V2" ]]; then
    if grep -q "task_parser.py.*verification-section" "$RALPH_V2" || grep -q "task_parser.py verification-section" "$RALPH_V2"; then
        pass "v2 script contains Python CLI call for verification-section"
    else
        fail "v2 script does not contain Python CLI call for verification-section"
    fi
else
    fail "v2 script not found (skipping test)"
fi

# Test 9: Verify v2 script does NOT contain bash parsing functions
test_start "v2 script does not contain bash parsing functions"
if [[ -f "$RALPH_V2" ]]; then
    # Check that the v2 script doesn't define these bash functions
    if grep -q "^get_indent()" "$RALPH_V2" || \
       grep -q "^is_checkbox()" "$RALPH_V2" || \
       grep -q "^is_incomplete()" "$RALPH_V2" || \
       grep -q "^is_complete()" "$RALPH_V2" || \
       grep -q "^has_subtasks()" "$RALPH_V2"; then
        fail "v2 script still contains bash parsing functions (should be removed)"
    else
        pass "v2 script does not contain bash parsing functions"
    fi
else
    fail "v2 script not found (skipping test)"
fi

# Test 10: Verify v2 script uses stable addressing (--line and --match)
test_start "v2 script uses stable addressing with --line and --match flags"
if [[ -f "$RALPH_V2" ]]; then
    # Check for usage of --line and --match together
    if grep -q "\-\-line.*\-\-match" "$RALPH_V2" || grep -q "\-\-match.*\-\-line" "$RALPH_V2"; then
        pass "v2 script uses stable addressing with --line and --match"
    else
        fail "v2 script does not use stable addressing with --line and --match"
    fi
else
    fail "v2 script not found (skipping test)"
fi

# Test 11: Functional test - verify Python CLI next-task works
test_start "Python CLI next-task command returns expected format"
create_test_task_file "$TEST_FILE"
output=$(python "$TASK_PARSER" next-task "$TEST_FILE" 2>&1 || true)
# Expected format: line_num|parent_text|task_text
if [[ "$output" =~ ^[0-9]+\|.*\|.* ]]; then
    pass "Python CLI next-task returns pipe-delimited format"
else
    fail "Python CLI next-task output format unexpected: $output"
fi

# Test 12: Functional test - verify Python CLI count works
test_start "Python CLI count command returns expected format"
output=$(python "$TASK_PARSER" count "$TEST_FILE" 2>&1 || true)
# Expected format: total|completed|failed
if [[ "$output" =~ ^[0-9]+\|[0-9]+\|[0-9]+ ]]; then
    pass "Python CLI count returns pipe-delimited format"
else
    fail "Python CLI count output format unexpected: $output"
fi

# Test 13: Functional test - verify Python CLI verification-section works
test_start "Python CLI verification-section command extracts content"
output=$(python "$TASK_PARSER" verification-section "$TEST_FILE" 2>&1 || true)
if [[ "$output" == *"All tests pass"* ]]; then
    pass "Python CLI verification-section extracts content correctly"
else
    fail "Python CLI verification-section output unexpected: $output"
fi

# Test 14: Functional test - verify Python CLI is-complete works
test_start "Python CLI is-complete command checks task status"
# Line 7 is "- [ ] 1.1 First subtask" which is incomplete
if python "$TASK_PARSER" is-complete "$TEST_FILE" --line 7 --match "First subtask" >/dev/null 2>&1; then
    fail "Python CLI is-complete returned 0 for incomplete task (expected 1)"
else
    exit_code=$?
    if [[ $exit_code -eq 1 ]]; then
        pass "Python CLI is-complete correctly returns 1 for incomplete task"
    else
        fail "Python CLI is-complete returned unexpected exit code: $exit_code"
    fi
fi

# Test 15: Functional test - verify Python CLI mark-complete and is-complete work together
test_start "Python CLI mark-complete and is-complete work together"
# Mark the task complete
if python "$TASK_PARSER" mark-complete "$TEST_FILE" --line 7 --match "First subtask" >/dev/null 2>&1; then
    # Now check if it's complete
    if python "$TASK_PARSER" is-complete "$TEST_FILE" --line 7 --match "First subtask" >/dev/null 2>&1; then
        pass "Python CLI mark-complete successfully marks task and is-complete detects it"
    else
        fail "Python CLI mark-complete/is-complete interaction failed: task not marked complete"
    fi
else
    fail "Python CLI mark-complete failed to mark task"
fi

# Summary
echo ""
echo "=========================================="
echo "Test Summary:"
echo "  Total tests run: $TESTS_RUN"
echo -e "  ${GREEN}Passed: $TESTS_PASSED${NC}"
echo -e "  ${RED}Failed: $TESTS_FAILED${NC}"
echo "=========================================="

if [[ $TESTS_FAILED -eq 0 ]]; then
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed!${NC}"
    exit 1
fi
