# PRD: Task Parser Rewrite — ralph-wiggum v2 with Python CLI

## Introduction / Overview

The `ralph-wiggum.sh` script is a sequential task executor that reads a markdown task file, extracts the next incomplete task, dispatches it to Claude Code headless mode, and loops until all tasks are done. Its task-parsing and file-mutation logic is currently implemented in bash using regex matching, `sed` replacements, and line-number arithmetic.

This approach has proven fragile:

- **Broken indent calculation** — a `[! ]` vs `[^ ]` bash glob misinterpretation caused all indentation calculations to return wrong values, making the script unable to distinguish parent tasks from leaf tasks.
- **Line-number drift** — `mark_task_failed` and `is_task_marked_complete` use the line number captured *before* Claude modifies the file. If Claude adds or removes lines (e.g., updating the Relevant Files section), `sed` edits the wrong line.
- **Single-level parent tracking** — `current_parent` only tracks one parent, so 3+ nesting levels lose the grandparent chain.
- **Brittle section detection** — duplicated header-matching logic across `get_next_task`, `count_tasks`, `auto_complete_parents`, and `get_verification_section`.
- **Last-line loss** — `while IFS= read -r line` silently drops the final line if the file lacks a trailing newline.

This PRD describes two deliverables:

1. **A standalone Python CLI** (`task-helpers/task_parser.py`) that handles all task-file parsing and mutation logic.
2. **A new v2 bash script** (`task-helpers/ralph-wiggum-v2.sh`) that uses the Python CLI for all file operations while keeping the orchestration logic (main loop, Claude invocation, retry logic).

The original `ralph-wiggum.sh` remains untouched as a stable fallback. The v2 script is a separate file that can be adopted when ready and the original removed at a later date once v2 is proven.

## Goals

1. **Eliminate line-number-based mutations** — identify tasks by content/structure, not line position, so edits are stable even when Claude modifies the file between calls.
2. **Correct indentation handling** — use Python string operations instead of bash glob patterns.
3. **Single source of parsing logic** — one parser implementation used by all commands, eliminating the duplicated section-detection code.
4. **Permissive format handling** — support the markdown variations that exist across the project's ~30 task files (see Format Support below).
5. **Testable** — unit tests in `task-helpers/tests/` covering parsing, mutation, and edge cases.
6. **Side-by-side deployment** — the v2 script (`ralph-wiggum-v2.sh`) coexists with the original; the original `ralph-wiggum.sh` is not modified.

## User Stories

1. **As a developer running ralph-wiggum**, I want the script to correctly identify the next leaf task regardless of indentation style or nesting depth, so that the loop doesn't skip tasks or execute parents.
2. **As a developer running ralph-wiggum**, I want task mutations (mark complete, mark failed) to target the correct task even after Claude has added or removed lines from the file, so that the script doesn't corrupt the task file.
3. **As a developer writing task files**, I want the parser to accept the markdown conventions I already use (with or without spaces in checkboxes, various heading levels for sections) without requiring me to reformat existing files.
4. **As a developer debugging ralph-wiggum**, I want to run individual parser commands (next-task, count, verify-section) in isolation to diagnose issues without running the full loop.

## Functional Requirements

### FR-1: CLI Interface

The tool is a single Python file (`task-helpers/task_parser.py`) invoked as:

```
python task-helpers/task_parser.py <command> <task-file> [options]
```

Commands:

| Command | Output (stdout) | Purpose |
|---------|-----------------|---------|
| `next-task` | `<line_num>\|<parent_text>\|<task_text>` | Return the next incomplete leaf task (same pipe-delimited format as today) |
| `count` | `<total>\|<completed>\|<failed>` | Count leaf tasks by status |
| `mark-complete <identifier>` | *(none on success)* | Mark a task `[x]` by line number or by unique substring match |
| `mark-failed <identifier>` | *(none on success)* | Mark a task `[!]` by line number or by unique substring match |
| `is-complete <identifier>` | Exit code 0 (yes) or 1 (no) | Check if a specific task is marked `[x]` |
| `auto-complete-parents` | List of auto-completed line numbers | Mark parent tasks `[x]` when all children are done |
| `verification-section` | Section content (stdout) | Extract the verification/criteria section text |
| `validate` | Validation warnings (stderr), exit 0 if parseable | Check file structure and report issues without modifying |

The `<identifier>` for mutation commands accepts either:
- A line number (integer) — for backward compatibility
- A quoted substring that uniquely matches one task's description — the safe default that survives line-number drift

### FR-2: Task File Parsing

The parser reads the full file and builds an in-memory tree:

- **Section detection**: Find the `## Tasks` section (case-insensitive match on heading text `tasks`; supports h1–h3). The section ends at the next heading of equal or higher level, or EOF.
- **Checkbox recognition**: Lines matching `- [ ]`, `- [x]`, `- [!]`, `- []` with any leading whitespace. Non-checkbox bullets (e.g., `` - `test_foo` — description``) are ignored.
- **Indentation**: Count leading spaces (tabs converted to 2 spaces for consistency). Indentation determines parent-child relationships.
- **Tree structure**: Each checkbox becomes a `Task` node. A task with more-indented checkbox children is a parent. A task with no checkbox children is a leaf. The tree supports arbitrary nesting depth.
- **Code block awareness**: Lines inside fenced code blocks (` ``` `) are excluded from parsing to avoid false checkbox matches.

### FR-3: Task Identification (Stable Addressing)

Each task is identified by:
1. Its current line number (1-based) — fast but fragile
2. Its description text — stable across line shifts

Mutation commands (`mark-complete`, `mark-failed`) first try the line-number match: if the line at that number is a checkbox whose text contains the expected description, use it. If the line has shifted (description doesn't match), fall back to scanning all tasks for a unique substring match. If zero or multiple matches are found, exit with a clear error.

### FR-4: File Mutation

All mutations operate on the in-memory representation and write the full file back atomically (write to temp file, then rename). This avoids partial-write corruption and eliminates `sed` line-number targeting.

- **mark-complete**: Change `[ ]` or `[]` to `[x]` on the matched task line. No other lines are modified.
- **mark-failed**: Change `[ ]` or `[]` to `[!]` on the matched task line.
- **auto-complete-parents**: After any mutation, walk the tree bottom-up. If all children of a parent are `[x]` or `[!]`, mark the parent `[x]`. Recurse for multi-level nesting.

### FR-5: Verification Section Extraction

Extract content between a heading matching `verification` or `verif` (case-insensitive prefix, h1–h3) and the next heading of equal or higher level (or EOF). Return the raw text content.

### FR-6: Validation Command

The `validate` command parses the file and reports warnings (not errors) for:
- No `## Tasks` section found
- Tasks section contains no checkboxes
- Mixed indentation (some tasks use 2-space indent, others use 4-space)
- No verification section found
- Unclosed code fences

Exit 0 if the file is parseable (warnings are informational). Exit 1 only if the file cannot be parsed at all.

### FR-7: ralph-wiggum-v2.sh

Create a new file `task-helpers/ralph-wiggum-v2.sh` by copying the orchestration logic from `ralph-wiggum.sh` (arg parsing, main loop, `run_claude`, retry logic, color/output helpers, dry-run logic, verification prompt, feature completion prompt) and replacing all bash parsing functions with Python CLI calls:

| Bash function (v1) | Python CLI call (v2) |
|---|---|
| `get_next_task` | `python task-helpers/task_parser.py next-task "$TASKS_FILE"` |
| `count_tasks` | `python task-helpers/task_parser.py count "$TASKS_FILE"` |
| `is_task_marked_complete "$line_num"` | `python task-helpers/task_parser.py is-complete "$TASKS_FILE" "$line_num"` |
| `mark_task_failed "$line_num"` | `python task-helpers/task_parser.py mark-failed "$TASKS_FILE" "$line_num"` |
| `auto_complete_parents` | `python task-helpers/task_parser.py auto-complete-parents "$TASKS_FILE"` |
| `get_verification_section` | `python task-helpers/task_parser.py verification-section "$TASKS_FILE"` |

The v2 script contains **no** task-parsing or file-mutation bash functions — all of that lives in the Python CLI. It keeps: `run_claude`, main loop, retry logic, arg parsing, color/output helpers, dry-run logic, verification prompt, feature completion prompt.

The original `ralph-wiggum.sh` is **not modified**. It remains as-is for fallback use.

After Claude reports a task complete, the v2 script should pass both the line number AND the task description to `is-complete`/`mark-failed`, enabling the stable addressing fallback:

```bash
python task-helpers/task_parser.py is-complete "$TASKS_FILE" --line "$line_num" --match "$task_desc"
```

## Format Support

The parser must handle these variations found across existing task files:

| Variation | Examples | Handling |
|---|---|---|
| Checkbox spacing | `- [ ]`, `- []`, `- [x]`, `- [!]` | All recognized |
| Heading levels | `# Tasks`, `## Tasks`, `### Tasks` | h1–h3 all match |
| Heading case | `## Tasks`, `## tasks` | Case-insensitive |
| Verification heading | `## Verification Criteria`, `# Verification criteria`, `## Verification` | Prefix match on `verif` |
| Bare verification checkboxes | `[] Check that X works` | Recognized as verification content (not as tasks) |
| Non-checkbox sub-bullets | `` - `test_foo` — description`` | Ignored by task parser |
| Code blocks containing checkboxes | ` ```\n- [ ] example\n``` ` | Excluded from parsing |
| Indentation levels | 2-space and 4-space | Both supported, measured by character count |

## Non-Goals

- **Markdown rendering** — this is a structural parser, not a renderer. It doesn't need to handle arbitrary markdown.
- **Multi-file operations** — each command operates on exactly one task file.
- **Task reordering or restructuring** — mutations only change checkbox state, never move or reorder lines.
- **Modifying ralph-wiggum.sh** — the original script is left untouched. It can be removed later once v2 is proven in use.

## Technical Considerations

- **Dependencies**: `markdown-it-py` for robust fenced-code-block detection, or `re`-based parsing if the code-fence handling stays simple enough. Use the project's `uv` tooling — the script can use inline script dependencies (`# /// script` PEP 723) so `uv run task-helpers/task_parser.py` auto-installs deps.
- **Atomic writes**: Use `tempfile.NamedTemporaryFile` + `os.replace` for crash-safe file mutation.
- **Encoding**: Assume UTF-8. Fail clearly on decode errors.
- **Exit codes**: 0 = success, 1 = no result (e.g., no next task), 2 = usage/argument error, 3 = file parse error.

## Success Metrics

1. All existing task files in `tasks/` (completed and active) parse without errors under the `validate` command.
2. `ralph-wiggum-v2.sh --print-only` produces correct task ordering on all existing task files (fixing the indent bugs present in v1).
3. Unit tests in `task-helpers/tests/` cover: basic parsing, nested parents, code-block exclusion, mixed indentation, line-number drift recovery, atomic write, all mutation commands, all format variations.
4. `ralph-wiggum-v2.sh` contains zero task-parsing or file-mutation bash functions — all file operations go through the Python CLI.
5. `ralph-wiggum.sh` (v1) is unchanged and remains runnable as a fallback.

## Open Questions

1. **Should `mark-complete` auto-run `auto-complete-parents`?** — Having it be automatic means the bash script makes one call instead of two. But keeping them separate is more explicit and easier to debug. Current recommendation: keep them separate (matches current bash behavior).
2. **Should the tool support `--json` output?** — Not needed now since ralph-wiggum parses pipe-delimited strings, but could be useful for future tooling. Defer unless requested.
