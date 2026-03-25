## Relevant Files

- `task-helpers/task_parser.py` - Main Python CLI for task-file parsing and mutation (new file, modified with Task dataclass, core parsing functions, next-task command implementation, count command implementation, verification-section command implementation, validate command implementation, mark-complete command implementation, mark-failed command implementation, is-complete command implementation, auto-complete-parents command implementation with multi-level cascading, refactored for consistent exit codes and uppercase X support, refactored to make identifier optional when --line or --match provided, and refactored to extract common logic from mark-complete and mark-failed into _mark_task_status helper).
- `task-helpers/tests/__init__.py` - Test package initialization (created).
- `task-helpers/tests/test_task_parser.py` - Unit tests for the task parser (new file, modified with section detection, checkbox recognition, indentation parsing, code-block awareness, next-task command tests, count command tests, verification-section command tests, validate command tests, stable addressing tests, line-number drift recovery tests, mark-complete function tests, mark-failed function tests, is-complete function tests, auto-complete-parents function tests, refactored to fix test inconsistencies, and fixed test assertion for stable addressing ambiguous descriptions test).
- `task-helpers/tests/test_integration.py` - Integration tests for validate command against various task file formats (new file, created with fixtures for simple, nested, complex, PRD-style, multiline, case-variation, and heading-level task files; enhanced with TestValidateExistingTaskFiles class that validates all existing tasks/**/*.md files in the repository and the project's PRD task file).
- `task-helpers/tests/test_bash_python_parity.py` - Integration tests comparing bash v1 functions to Python CLI output for next-task and count commands (new file, created with comprehensive test cases for simple, nested, complex, and edge-case task files; uses bash_function_wrapper.sh to call original bash functions from ralph-wiggum.sh; updated to handle Windows CRLF line-ending artifacts in bash output and to document known bash v1 bugs that Python has fixed, including uppercase X checkbox support and proper parent task detection).
- `task-helpers/tests/bash_function_wrapper.sh` - Wrapper script to extract and call bash functions from ralph-wiggum.sh for integration testing (new file, extracts function definitions and provides callable interface for test suite).
- `task-helpers/tests/test_ralph_wiggum_v2.sh` - Shell-level integration tests for ralph-wiggum-v2.sh (new file).
- `task-helpers/ralph-wiggum-v2.sh` - V2 bash script using Python CLI for all file operations (new file, copied from ralph-wiggum.sh, modified to replace all bash parsing functions with Python CLI calls).
- `task-helpers/ralph-wiggum.sh` - Original bash script (read-only reference, NOT modified).

### Notes

- **This project follows Test-Driven Development (TDD).** For each feature or component, write the tests first, verify they fail, then write the implementation to make them pass.
- Unit tests should be placed in `task-helpers/tests/` as specified by the PRD.
- Use `uv run pytest task-helpers/tests/` to run tests.
- The original `ralph-wiggum.sh` must **not** be modified at any point.

## Tasks

- [x] 1.0 Set up project structure, dependencies, and CLI skeleton
  - [x] 1.1 Create `task-helpers/tests/` directory and `task-helpers/tests/__init__.py`
  - [x] 1.2 Create `task-helpers/task_parser.py` with PEP 723 inline script metadata and argparse skeleton (commands defined but returning stubs)
  - [x] 1.3 Write tests for CLI argument parsing and help output (`test_task_parser.py`)
  - [x] 1.4 Run tests to confirm they fail (red phase — validates tests are non-trivial)
  - [x] 1.5 Implement CLI argparse to make tests pass (green phase)
  - [x] 1.6 Verify `uv run task-helpers/task_parser.py --help` prints usage and exits 0

- [x] 2.0 Implement core task-file parser (section detection, checkbox recognition, tree building)
  - [x] 2.1 Write tests for section detection: find `## Tasks` heading (h1–h3, case-insensitive), stop at next equal-or-higher heading or EOF
  - [x] 2.2 Write tests for checkbox recognition: `- [ ]`, `- []`, `- [x]`, `- [!]` with any leading whitespace; non-checkbox bullets ignored
  - [x] 2.3 Write tests for indentation parsing and parent-child tree building (2-space, 4-space, arbitrary nesting depth)
  - [x] 2.4 Write tests for code-block awareness: checkboxes inside fenced code blocks are excluded
  - [x] 2.5 Run tests to confirm they fail (red phase — validates tests are non-trivial)
  - [x] 2.6 Implement `Task` data class and `parse_task_file()` function to build in-memory tree (FR-2)
  - [x] 2.7 Implement code-block exclusion logic
  - [x] 2.8 Refactor if needed while keeping tests green

- [x] 3.0 Implement read-only CLI commands (next-task, count, verification-section, validate)
  - [x] 3.1 Write tests for `next-task`: returns pipe-delimited `line_num|parent_text|task_text` for next incomplete leaf; exits 1 when no tasks remain
  - [x] 3.2 Write tests for `count`: returns `total|completed|failed` counting only leaf tasks
  - [x] 3.3 Write tests for `verification-section`: extracts content between verification heading (prefix match on `verif`, case-insensitive, h1–h3) and next heading/EOF (FR-5)
  - [x] 3.4 Write tests for `validate`: warns on missing Tasks section, no checkboxes, mixed indentation, no verification section, unclosed code fences; exits 0 if parseable, 1 if not (FR-6)
  - [x] 3.5 Run tests to confirm they fail (red phase — validates tests are non-trivial)
  - [x] 3.6 Implement `next-task` command
  - [x] 3.7 Implement `count` command
  - [x] 3.8 Implement `verification-section` command
  - [x] 3.9 Implement `validate` command
  - [x] 3.10 Refactor if needed while keeping tests green

- [x] 4.0 Implement mutation commands with stable addressing and atomic writes (mark-complete, mark-failed, is-complete, auto-complete-parents)
  - [x] 4.1 Write tests for stable addressing (FR-3): identifier as line number with description verification, fallback to unique substring scan, error on zero/multiple matches
  - [x] 4.2 Write tests for `mark-complete`: changes `[ ]`/`[]` to `[x]`, atomic write via tempfile+rename (FR-4)
  - [x] 4.3 Write tests for `mark-failed`: changes `[ ]`/`[]` to `[!]`, atomic write (FR-4)
  - [x] 4.4 Write tests for `is-complete`: exit 0 if `[x]`, exit 1 otherwise
  - [x] 4.5 Write tests for `auto-complete-parents`: bottom-up walk, mark parent `[x]` when all children are `[x]`/`[!]`, recurse for multi-level nesting
  - [x] 4.6 Write tests for line-number drift recovery: line shifted but substring match finds correct task
  - [x] 4.7 Run tests to confirm they fail (red phase — validates tests are non-trivial)
  - [x] 4.8 Implement stable addressing resolver (FR-3)
  - [x] 4.9 Implement atomic file write helper (tempfile + os.replace) (FR-4)
  - [x] 4.10 Implement `mark-complete` command
  - [x] 4.11 Implement `mark-failed` command
  - [x] 4.12 Implement `is-complete` command
  - [x] 4.13 Implement `auto-complete-parents` command
  - [x] 4.14 Refactor if needed while keeping tests green

- [!] 5.0 Create ralph-wiggum-v2.sh using the Python CLI for all file operations
  - [x] 5.1 Write tests (shell-level integration tests) verifying v2 script calls Python CLI for each operation: next-task, count, is-complete, mark-failed, auto-complete-parents, verification-section
  - [x] 5.2 Run tests to confirm they fail (red phase — validates tests are non-trivial)
  - [x] 5.3 Copy `ralph-wiggum.sh` to `ralph-wiggum-v2.sh`
  - [x] 5.4 Replace all bash parsing functions (`get_next_task`, `count_tasks`, `is_task_marked_complete`, `mark_task_failed`, `auto_complete_parents`, `get_verification_section`) with Python CLI calls per FR-7 mapping
  - [x] 5.5 Remove bash helper functions (`get_indent`, `is_checkbox`, `is_incomplete`, `is_complete`, `has_subtasks`)
  - [x] 5.6 Update `is-complete`/`mark-failed` calls to pass both `--line` and `--match` for stable addressing
  - [x] 5.7 Verify `ralph-wiggum.sh` is completely unchanged (diff against git HEAD)

- [x] 6.0 End-to-end validation against existing task files and integration testing
  - [x] 6.1 Write integration tests that run `validate` against all existing `tasks/**/*.md` files — all should parse without errors
  - [x] 6.2 Write integration tests that compare `next-task` and `count` output between v1 bash functions and Python CLI on the same task files
  - [x] 6.3 Run tests to confirm they fail (red phase — validates tests are non-trivial)
  - [x] 6.4 Implement integration test fixtures and run all tests
  - [x] 6.5 Run `ralph-wiggum-v2.sh --print-only` against existing task files and verify correct task ordering
  - [x] 6.6 Fix any parsing edge cases discovered during integration testing

## Verification Criteria

How we know the tasks have been successfully implemented:

- [ ] `uv run pytest task-helpers/tests/` passes all tests
- [ ] `uv run task-helpers/task_parser.py validate <file>` exits 0 on all existing task files in `tasks/`
- [ ] `uv run task-helpers/task_parser.py next-task` correctly identifies the next leaf task on nested task files
- [ ] `uv run task-helpers/task_parser.py count` matches expected counts on known task files
- [ ] `mark-complete` and `mark-failed` survive line-number drift (tested with modified files where lines have been inserted/removed)
- [ ] `auto-complete-parents` correctly cascades through 3+ nesting levels
- [ ] `ralph-wiggum-v2.sh --print-only` produces correct output on existing task files
- [ ] `ralph-wiggum.sh` is identical to its git HEAD version (not modified)
- [ ] `ralph-wiggum-v2.sh` contains zero bash task-parsing or file-mutation functions — all file operations go through the Python CLI
