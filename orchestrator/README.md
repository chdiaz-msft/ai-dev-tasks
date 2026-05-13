# Orchestrator — Technical Internals

> Internal reference for contributors working on the PR Flywheel orchestrator package.
> For adoption and usage, see the [top-level README](../README.md).

## Package Overview

The `orchestrator/` package implements the core logic of the PR Flywheel loop. It is pure Python (≥ 3.11) with two external dependencies (`PyGithub`, `anthropic`).

```text
orchestrator/
├── models.py                Data models (Issue, LoopState, enums)
├── review_swarm.py          Parallel reviewer dispatch (Anthropic API)
├── signal_aggregator.py     Signal normalization and fusion
├── severity_classifier.py   Severity scale, comparison, and filtering
├── flywheel_controller.py   Decision engine and CLI entry point
├── fix_dispatcher.py        Fix agent routing and safety gates
├── requirements.txt         Python dependencies
└── tests/                   Comprehensive test suite
    ├── test_models.py
    ├── test_review_swarm.py
    ├── test_signal_aggregator.py
    ├── test_severity_classifier.py
    ├── test_flywheel_controller.py
    ├── test_fix_dispatcher.py
    └── test_integration.py
```

---

## Data Structures

### `TerminationStatus` (StrEnum)

Terminal states for the flywheel loop, persisted in `loop-state.json`.

| Value | Meaning |
| --- | --- |
| `success` | All issues resolved, checks green |
| `handoff` | Max iterations reached with unresolved issues |
| `blocked` | Human submitted "Request Changes" |
| `waiting` | Pending external signal (checks, reviewer coverage, poll interval) |

### `IssueStatus` (StrEnum)

Lifecycle states for a single review finding.

| Value | Meaning |
| --- | --- |
| `open` | Active finding; may be actionable depending on severity floor |
| `resolved` | Finding cleared automatically or manually |
| `wont_fix` | Intentionally not addressed (model-level only; not automated) |
| `escalated` | Escalated to human review (model-level only; not automated) |

### `Issue` (dataclass)

Represents a single review finding tracked across iterations.

| Field | Type | Description |
| --- | --- | --- |
| `issue_id` | `str` | Deterministic ID: `{reviewer}-{sha256(file+line+issue)[:8]}` |
| `reviewer` | `str` | Name of the reviewer that produced this finding |
| `severity` | `str` | One of: `critical`, `high`, `medium`, `low`, `info` |
| `issue` | `str` | Human-readable description of the problem |
| `suggested_fix` | `str` | Recommended remediation |
| `file` | `str` | File path where the issue was found |
| `line` | `int \| None` | Line number (None for file-level issues) |
| `found_in_round` | `int` | Iteration when first detected |
| `status` | `str` | Current `IssueStatus` value (default: `"open"`) |
| `resolved_in_round` | `int \| None` | Iteration when resolved (None if still open) |
| `resolution` | `str \| None` | How the issue was resolved (None if still open) |

Serialization: `to_dict()` / `from_dict()` for JSON persistence.

### `LoopState` (dataclass)

Persistent state across flywheel iterations, stored at `state/loop-state.json`.

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `pr_number` | `int` | — | Pull request number |
| `current_round` | `int` | `0` | Current iteration (incremented at start of each run) |
| `max_rounds` | `int` | `5` | Maximum allowed iterations |
| `issues` | `dict[str, Issue]` | `{}` | All tracked issues keyed by `issue_id` |
| `termination` | `str \| None` | `None` | Terminal status when loop stops |
| `last_round_timestamp` | `str \| None` | `None` | ISO 8601 timestamp of last iteration |

Serialization: `to_dict()` / `from_dict()` for JSON persistence.

---

## Decision Engine (`flywheel_controller.py`)

The controller is the orchestrator's main entry point. It loads state, merges findings, resolves issues, and produces the next action.

### `decide()` Priority Chain

The decision function evaluates conditions in strict priority order. The **first** matching condition wins:

1. **Poll interval not elapsed** (5 min minimum between rounds, skipped for round 1) → `waiting`
2. **Max rounds reached** (`current_round >= max_rounds`) → `handoff`
3. **Human rejection** (any review with `state == "CHANGES_REQUESTED"`) → `blocked`
4. **Actionable issues exist** (open issues at ≥ severity floor) → `fixing` (dispatch capped at 10 items)
5. **Checks not green** (any check without `success`/`neutral`/`skipped` conclusion) → `waiting`
6. **Incomplete reviewer coverage** (failed reviewers in coverage report) → `waiting`
7. **All clear** (no actionable issues, checks green, full coverage) → `ready`

### Issue Resolution

Three automatic resolution methods during `merge_findings()`:

| Method | Trigger | Resolution text |
| --- | --- | --- |
| **Reviewer absence** | Reviewer ran again but no longer flags the `issue_id` | `"No longer flagged by {reviewer} in round {n}"` |
| **Commit annotation** | Commit message contains `[resolves: issue_id]` | `"Marked resolved via commit message"` |
| **Dismissed thread** | Matching review thread (same file, line ±5) was resolved/dismissed | `"Resolved via dismissed review thread"` |

### State Persistence

- State file: `state/loop-state.json`
- Loaded at start, incremented, written after decision
- GitHub Actions cache provides cross-run persistence

### CLI Interface

```bash
python -m orchestrator.flywheel_controller \
  --signals signals.json \
  --max-iter 5 \
  --severity-floor high \
  --out decision.json
```

Outputs:
- `decision.json` — decision dict with `state`, `reason`, optional `dispatch`
- `GITHUB_OUTPUT` — workflow outputs: `state`, `iteration`, `dispatch`, `has_changes`

---

## Severity Classifier (`severity_classifier.py`)

### Severity Scale

| Level | Numeric | Auto-fix eligible |
| --- | --- | --- |
| `critical` | 4 | Always |
| `high` | 3 | Yes (default floor) |
| `medium` | 2 | If floor ≤ medium |
| `low` | 1 | Never auto-fixed |
| `info` | 0 | Never auto-fixed |

### Key Functions

| Function | Purpose |
| --- | --- |
| `severity_gte(a, b)` | Returns `True` if severity `a` ≥ `b` |
| `severity_lt(a, b)` | Returns `True` if severity `a` < `b` |
| `filter_by_floor(issues, floor)` | Returns only issues meeting the severity threshold |
| `classify_severity(raw)` | Normalizes raw string to canonical level; unknown → `info` |

---

## Signal Aggregator (`signal_aggregator.py`)

Fuses signals from 6 sources into a unified `signals.json` for the decision engine.

### Signal Sources

| Source | Method | Data extracted |
| --- | --- | --- |
| Review swarm | JSON file (`swarm_result.json`) | Findings list + reviewer coverage |
| CI check runs | PyGithub (`get_check_runs()`) | name, conclusion, details_url, output_summary |
| Human/AI reviews | PyGithub (`get_reviews()`) | id, state, author, body; classified as human or AI |
| Review comments | PyGithub (`get_review_comments()`) | Same schema; classified by user type |
| Code scanning alerts | `gh api` CLI | rule, severity, path |
| Review threads | GraphQL via `gh api graphql` | path, line, body, author; split into resolved/unresolved |

### GraphQL Query

Fetches review threads (first 100) with resolution status:

```graphql
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          isResolved
          comments(first: 1) {
            nodes { path, position, body, author { login } }
          }
        }
      }
    }
  }
}
```

### Output Schema (`signals.json`)

```json
{
  "pr_number": 42,
  "head_sha": "abc123...",
  "swarm_findings": [...],
  "reviewer_coverage": { "expected": [...], "completed": [...], "failed": [...] },
  "checks": [{ "name": "...", "conclusion": "...", "details_url": "...", "output_summary": "..." }],
  "human_reviews": [{ "id": 1, "state": "...", "author": "...", "body": "..." }],
  "ai_reviews": [...],
  "unresolved_threads": [{ "path": "...", "line": 5, "body": "...", "author": "..." }],
  "resolved_threads": [{ "path": "...", "line": 5, "body": "..." }],
  "security": [{ "rule": "...", "severity": "...", "path": "..." }]
}
```

### CLI Interface

```bash
python -m orchestrator.signal_aggregator --swarm-findings swarm_result.json
```

Required environment variables: `GH_TOKEN`, `REPO` (owner/name), `PR_NUMBER`.

---

## Review Swarm (`review_swarm.py`)

Dispatches specialized reviewers in parallel via the Anthropic Claude API.

### Architecture

```text
dispatch_swarm()
  │
  ├── ThreadPoolExecutor(max_workers=len(REVIEWERS))
  │     ├── run_reviewer("correctness", ...) ──► Anthropic API
  │     └── run_reviewer("security", ...)    ──► Anthropic API
  │
  └── Collect results (120s global timeout)
        ├── Success: extend findings list
        └── Failure: record in failed_reviewers
```

### Key Constants

| Constant | Value | Purpose |
| --- | --- | --- |
| `REVIEWERS` | `["correctness", "security"]` | List of reviewers to dispatch |
| `TIMEOUT_SECONDS` | `120` | Global timeout for all reviewers |
| `_API_TIMEOUT_SECONDS` | `90` | Per-request Anthropic API timeout |

### Deterministic Issue IDs

Generated by `generate_issue_id()`:

```
Format: {reviewer}-{sha256(file + line + issue_text)[:8]}
Example: correctness-a1b2c3d4
```

This ensures stable tracking across iterations — the same finding in the same location always produces the same ID.

### Graceful Degradation

- Individual reviewer failures are caught and recorded in `reviewer_coverage.failed`
- The swarm returns partial results from successful reviewers
- The decision engine treats failed reviewers as "incomplete coverage" → `waiting`

### Model

Uses `claude-sonnet-4-20250514` with `max_tokens=4096`.

---

## Fix Dispatcher (`fix_dispatcher.py`)

Routes fix operations to the selected agent with safety constraints.

### Agent Selection

```python
select_agent(enable_claude=True, enable_copilot_agent=False) → "claude"
select_agent(enable_claude=False, enable_copilot_agent=True) → "copilot"
select_agent(enable_claude=True, enable_copilot_agent=True)  → "claude"  # preferred
select_agent(enable_claude=False, enable_copilot_agent=False) → ValueError
```

### Dispatch Payload

Built by `build_dispatch_payload()`:

```json
{
  "agent": "claude",
  "items": [...],          // capped at 10 items
  "prompt_path": "prompts/claude-fix-prompt.md"
}
```

- **Payload cap**: Maximum 10 items per dispatch to limit blast radius
- **Prompt path**: Set to `prompts/claude-fix-prompt.md` for Claude; `None` for Copilot

### Forbidden Path Validation

`validate_forbidden_paths()` blocks commits that touch:

| Pattern | Blocks |
| --- | --- |
| `^\.github/workflows/` | Any workflow file |
| `secrets` | Any path containing "secrets" |

`get_forbidden_files()` returns the specific files that would be blocked.

---

## Extension Points

### Adding a New Reviewer

1. **Create the prompt**: Add `prompts/reviewers/{name}.md` following the output schema defined in `prompts/REVIEW.md`.

2. **Register the reviewer**: Add the name to the `REVIEWERS` list in `review_swarm.py`:
   ```python
   REVIEWERS = ["correctness", "security", "your-new-reviewer"]
   ```

3. **Done**: The swarm automatically dispatches it in parallel alongside existing reviewers. No other code changes required.

### Adding a New Signal Source

1. Add a fetcher function in `signal_aggregator.py` (following the pattern of `_run_gh_cli` or PyGithub calls).
2. Add the new data to the `build_signals()` output dict.
3. If the signal affects decisions, update `decide()` in `flywheel_controller.py`.

### Adding a New Resolution Method

1. Write a `resolve_issues_from_*()` function in `flywheel_controller.py`.
2. Call it from `main()` after `merge_findings()`.
3. Set `status = "resolved"`, `resolved_in_round`, and `resolution` on matched issues.

---

## Test Suite

### Running Tests

```bash
# All orchestrator tests
uv run pytest orchestrator/tests/

# Single module
uv run pytest orchestrator/tests/test_flywheel_controller.py -v

# Integration tests
uv run pytest orchestrator/tests/test_integration.py -v

# With coverage
uv run pytest orchestrator/tests/ --cov=orchestrator --cov-report=term-missing
```

### Test Organization

| Test file | Covers |
| --- | --- |
| `test_models.py` | Issue/LoopState serialization, enum values |
| `test_review_swarm.py` | Issue ID generation, parallel dispatch, timeout handling |
| `test_signal_aggregator.py` | Signal normalization, deduplication, GraphQL parsing |
| `test_severity_classifier.py` | Severity comparisons, filtering, classification |
| `test_flywheel_controller.py` | Decision logic, state management, issue resolution |
| `test_fix_dispatcher.py` | Agent selection, payload building, path validation |
| `test_integration.py` | End-to-end workflow scenarios |

Tests use standard `pytest` with fixtures in `tests/fixtures/`. No external services are required — API calls are mocked.
