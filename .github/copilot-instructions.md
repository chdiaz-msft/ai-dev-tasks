# Copilot Instructions

## Project Overview

This repository is a **prompt and tooling collection** for structured AI-assisted feature development. It provides markdown prompt files that guide AI coding assistants through a PRD → Task List → Implementation workflow using TDD. It also includes `ralph-wiggum`, a bash-based automation harness that executes task lists sequentially via GitHub Copilot CLI prompt mode.

## Architecture

### Prompt Files (`task-helpers/`)

Three core prompt files drive the workflow. They use Claude's frontmatter format (`---` header with `description`, `globs`, `alwaysApply`):

- `task-helpers/create-prd.prompt.md` — Guides PRD creation. Saves output to `/tasks/[feature-name]/prd-[feature-name].md`
- `task-helpers/generate-tasks.prompt.md` — Converts a PRD into a TDD task list. Saves to `/tasks/tasks-[prd-file-name].md`
- `task-helpers/complete-feature.prompt.md` — Verifies completion, renames files with `completed-` prefix, moves to `tasks/completed/`

### Task Execution (`task-helpers/`)

- `ralph-wiggum-v2.sh` — Bash script that reads a markdown task file and executes each task via GitHub Copilot CLI prompt mode. Supports `--model`, `--max-retries`, `--max-ai-credits`, `--system-prompt-file`, `--selfcorrect`, `--print-only`, and `--verbose` flags.
- `task_parser.py` — Python CLI that parses markdown checkbox task files. Replaces fragile bash parsing with robust regex-based extraction. Zero external dependencies (stdlib only, Python ≥3.9).
- `process-task-list.prompt.md` — Prompt governing how AI works through tasks one sub-task at a time, using `[ ]`, `[x]`, and `[!]` markers.

### Code Review (`python_code_review_guidelines.md`, `.github/skills/python-code-review/`)

A parallel code review system dispatches 5 specialized agents across tracks: correctness, type safety, architecture, tests, and production readiness. Guidelines reference `ruff` for linting, `pyright` in strict mode for types, and `uv run pytest` for tests.

## Build & Test

The Python code in `task-helpers/` has no external dependencies. Run tests with:

```bash
# All tests
uv run pytest task-helpers/tests/

# Single test file
uv run pytest task-helpers/tests/test_task_parser.py

# Single test
uv run pytest task-helpers/tests/test_task_parser.py::TestCLIHelpOutput::test_help_flag_exits_zero
```

All Python commands should run through `uv` (e.g., `uv run python`, `uv run pytest`).

## Key Conventions

- **TDD workflow**: Tests are written before implementation. Every parent task follows Red-Green-Refactor: write tests → run to confirm failure → implement → refactor.
- **Task checkbox syntax**: `[ ]` = incomplete, `[x]` = complete, `[!]` = blocked/infeasible (with reason). Parent tasks auto-complete when all subtasks resolve.
- **File organization**: PRDs and task lists live under `/tasks/[feature-name]/`. Completed features are renamed with `completed-` prefix and moved to `tasks/completed/`.
- **One sub-task at a time**: When executing a task list, complete one sub-task, update the markdown file, then pause for user approval before proceeding.
- **Python style**: Use `ruff` for formatting/linting, `pyright` in strict mode (all functions need parameter and return types). Prefer `FrozenBaseModel`/`dataclass` over raw dicts for known schemas.
