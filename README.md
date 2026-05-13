# PR Flywheel Template

> A practical, ship-today blueprint for an autonomous PR improvement loop using free / open / GHEC-native tools.

## What it is

The PR Flywheel is a bounded, autonomous review-and-fix loop for pull requests. It reviews a PR, aggregates signals from multiple sources, decides whether to fix or wait, dispatches an AI fixer, and repeats — until the PR is ready, blocked, or handed off to a human.

It is designed for teams that want an auditable automation loop built on GitHub-native workflows with optional Claude and Copilot integrations.

## How it works

The loop runs as a GitHub Actions workflow triggered by PR events. Each iteration executes six stages:

```text
┌─────────────────────────────────────────────────────────────────────────┐
│                          PR Flywheel Loop                               │
│                                                                         │
│  PR event ──► Review   ──► Aggregate  ──► Decide  ──► Fix    ──► Push  │
│  (trigger)    Swarm        Signals        Engine      Agent      & Loop │
│                                                                         │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                    Persisted Artifacts                              │ │
│  │  state/loop-state.json   signals.json   decision.json              │ │
│  │  swarm_result.json       (GitHub Actions cache + artifacts)        │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
```

1. **Review Swarm** — Dispatch specialized reviewers (correctness, security) in parallel via the Anthropic API. Each returns structured JSON findings with deterministic issue IDs.
2. **Aggregate Signals** — Merge swarm findings with CI check runs, human/AI PR reviews, code scanning alerts, and review thread state into a unified `signals.json`.
3. **Decision Engine** — Load persisted loop state, merge new findings, resolve completed issues, and decide the next action based on severity filtering and signal analysis.
4. **Fix Agent** — If the decision is `fixing`, dispatch Claude Code or GitHub Copilot to implement the fixes. The agent reads `decision.json` for the issue list and `prompts/claude-fix-prompt.md` for operating rules.
5. **Commit & Push** — Validate changed paths against the forbidden-path allowlist, commit with `[resolves: issue_id]` annotations, and push to the PR branch.
6. **Loop** — The push triggers a new `synchronize` event, restarting the workflow for the next iteration.

## Quick start

### 1. Create a caller workflow

In your repository, create `.github/workflows/pr-flywheel.yml`:

```yaml
name: pr-flywheel
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]
  pull_request_review:
    types: [submitted]
  check_suite:
    types: [completed]

jobs:
  flywheel:
    uses: your-org/pr-flywheel-template/.github/workflows/flywheel.yml@v1
    with:
      max_iterations: 5
      severity_floor: high
      enable_claude: true
      enable_copilot_agent: false
    secrets: inherit
```

### 2. Configure secrets

Add these secrets to your repository or organization:

| Secret | Required | Description |
| --- | --- | --- |
| `FLYWHEEL_APP_ID` | Yes | GitHub App ID for the flywheel bot |
| `FLYWHEEL_APP_KEY` | Yes | GitHub App private key (PEM) |
| `ANTHROPIC_API_KEY` | If Claude enabled | Anthropic API key for reviews and fixes |

### 3. Configure the GitHub App

The flywheel bot GitHub App needs these permissions:

| Permission | Access | Purpose |
| --- | --- | --- |
| Contents | Read & Write | Read diffs, push fix commits |
| Pull requests | Read & Write | Read PR metadata, post comments |
| Checks | Read & Write | Read check runs, publish `flywheel/merge-ready` |
| Issues | Write | Post status comments |
| Security events | Read | Read code scanning alerts |
| Actions | Write | (nightly babysitter only) re-trigger workflows |

### 4. Configure branch protection

- Allow the flywheel bot app to push to PR branches.
- Optionally require the `flywheel/merge-ready` check for merge.
- Ensure the flywheel workflow itself is not a required check (to avoid self-blocking).

### 5. Open a PR

The flywheel activates automatically on PR open, synchronize, review submission, and check suite completion events.

## Configuration

| Input | Default | Description |
| --- | --- | --- |
| `max_iterations` | `5` | Maximum flywheel rounds before automatic handoff to humans. |
| `severity_floor` | `high` | Lowest severity that becomes actionable. Issues below this threshold are reported but not auto-fixed. |
| `enable_claude` | `true` | Enable Claude Code as the fix agent (preferred when both are enabled). |
| `enable_copilot_agent` | `false` | Enable GitHub Copilot agent as the fix agent. |

### Custom review doctrine

Drop a `prompts/copilot-instructions.md` in your repo to customize reviewer behavior. See `prompts/copilot-instructions.md` in this template for the full schema covering:

- Severity overrides and path exclusions
- Reviewer-specific focus areas
- Human escalation rules
- Testing requirements and commit style

## Architecture

### Data flow

```text
PR event (opened/synchronize/review/check_suite)
  │
  ▼
┌──────────────────────┐
│  flywheel.yml        │─── Restore loop-state.json from cache
│  (GitHub Actions)    │─── Check flywheel:pause label
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐     prompts/reviewers/*.md
│  review_swarm.py     │◄─── (reviewer system prompts)
│  Parallel dispatch   │───► swarm_result.json
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐     GitHub API (PyGithub + gh CLI)
│  signal_aggregator.py│◄─── checks, reviews, code scanning,
│  Signal fusion       │     review threads (GraphQL)
│                      │───► signals.json
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ flywheel_controller  │◄─── state/loop-state.json
│ Decision engine      │───► decision.json
│                      │───► GITHUB_OUTPUT (state, iteration,
└──────────┬───────────┘     dispatch, has_changes)
           │
           ├── fixing ──► Claude Code Action or Copilot Agent
           │              ───► validate paths ──► commit + push
           │                                      (triggers next loop)
           │
           ├── ready ───► Publish flywheel/merge-ready check
           │              Post completion comment
           │
           ├── waiting ─► Post waiting comment (retry later)
           │
           ├── blocked ─► Post blocked comment (human rejection)
           │
           └── handoff ─► Post handoff comment (max iterations)
```

For decision engine states, issue lifecycle, severity scale, and all other orchestrator internals, see [`orchestrator/README.md`](orchestrator/README.md).

## Safety rails

- **Bounded iterations** — Maximum 5 rounds by default; configurable via `max_iterations`.
- **Severity-floor filtering** — Only issues at or above the threshold trigger fixes; low/info are never auto-fixed.
- **Forbidden paths** — The fix agent and post-fix validation block edits to `.github/workflows/**` and any path containing `secrets`.
- **Dispatch cap** — At most 10 issues per fix dispatch to limit blast radius.
- **Poll interval** — 5-minute minimum between iterations to prevent tight loops.
- **Kill switch** — Add the `flywheel:pause` label to any PR to halt the loop immediately.
- **Human veto** — A "Request Changes" review from a human blocks all automation.
- **Concurrency control** — Only one flywheel run per PR at a time (`cancel-in-progress: true`).
- **Fork protection** — The workflow only runs on branches from the same repository (not fork PRs).
- **Audit trail** — Every round uploads `decision.json`, `signals.json`, `swarm_result.json`, and `loop-state.json` as GitHub Actions artifacts (30-day retention).

## Workflows

### `flywheel.yml` — Main reusable workflow

A `workflow_call` workflow that implements the full review-fix loop. Consumer repos call this from their own workflow file.

**Trigger events supported:** `pull_request`, `pull_request_review`, `check_suite`

### `flywheel-nightly.yml` — Babysitter workflow

A scheduled workflow that runs daily at 06:00 UTC (and on manual dispatch). It finds open PRs with the `flywheel` label that have been stale for >24 hours with no recent flywheel check runs, and re-triggers the flywheel workflow to unstick them.

## Prompts and reviewers

```text
prompts/
├── REVIEW.md                  # Default review doctrine (severity scale,
│                              # output schema, general rules)
├── claude-fix-prompt.md       # Fix agent operating rules
├── copilot-instructions.md    # Custom doctrine template for consumer repos
└── reviewers/
    ├── correctness.md         # Correctness reviewer system prompt
    └── security.md            # Security reviewer system prompt
```

**Adding a new reviewer:** See [`orchestrator/README.md` — Extension Points](orchestrator/README.md#extension-points).

## Module reference

| Module | Role |
| --- | --- |
| [`models.py`](orchestrator/models.py) | Data models (`Issue`, `LoopState`, enums) |
| [`review_swarm.py`](orchestrator/review_swarm.py) | Parallel reviewer dispatch |
| [`signal_aggregator.py`](orchestrator/signal_aggregator.py) | Signal normalization and fusion |
| [`severity_classifier.py`](orchestrator/severity_classifier.py) | Severity scale and filtering |
| [`flywheel_controller.py`](orchestrator/flywheel_controller.py) | Decision engine (main entry point) |
| [`fix_dispatcher.py`](orchestrator/fix_dispatcher.py) | Fix agent routing and safety gates |

See [`orchestrator/README.md`](orchestrator/README.md) for detailed internals on each module.

## Repository layout

```text
orchestrator/                  Core Python package
├── models.py                  Data models (Issue, LoopState)
├── review_swarm.py            Parallel reviewer dispatch
├── signal_aggregator.py       Signal normalization and fusion
├── severity_classifier.py     Severity scale and filtering
├── flywheel_controller.py     Decision engine (main entry point)
├── fix_dispatcher.py          Fix agent routing and safety gates
├── requirements.txt           Python dependencies (PyGithub, anthropic)
└── tests/                     Comprehensive test suite
    ├── test_models.py
    ├── test_review_swarm.py
    ├── test_signal_aggregator.py
    ├── test_severity_classifier.py
    ├── test_flywheel_controller.py
    ├── test_fix_dispatcher.py
    └── test_integration.py

prompts/                       Prompt assets
├── REVIEW.md                  Default review doctrine
├── claude-fix-prompt.md       Fix agent operating rules
├── copilot-instructions.md    Custom doctrine template
└── reviewers/                 Per-reviewer system prompts
    ├── correctness.md
    └── security.md

.github/workflows/
├── flywheel.yml               Main reusable workflow
└── flywheel-nightly.yml       Nightly babysitter for stuck PRs

state/                         Runtime state (gitignored, cached)
└── loop-state.json            Persisted loop state per PR
```

## Development

### Run tests

```bash
# All orchestrator tests
uv run pytest orchestrator/tests/

# Single test file
uv run pytest orchestrator/tests/test_flywheel_controller.py -v

# Integration tests
uv run pytest orchestrator/tests/test_integration.py -v
```

### Dependencies

Python ≥ 3.11 with:
- `PyGithub >= 2.3.0` — GitHub API access for checks, reviews, and PR metadata
- `anthropic >= 0.30.0` — Anthropic Claude API for reviewer dispatch

Install via: `pip install -r orchestrator/requirements.txt`

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Flywheel stays in `waiting` | Checks not green, or incomplete reviewer coverage | Check CI status; verify `ANTHROPIC_API_KEY` is set |
| Flywheel goes to `handoff` | Max iterations reached with unresolved issues | Increase `max_iterations` or fix issues manually |
| Flywheel `blocked` | A human submitted "Request Changes" | Resolve the review or dismiss it |
| No findings produced | Missing `ANTHROPIC_API_KEY` or SDK not installed | Set the secret; check workflow logs |
| Forbidden path error | Fix agent tried to edit a workflow or secrets file | Review the fix agent output; the safety gate prevented the commit |
| PR stuck with no activity | Workflow not triggered or hit concurrency limits | Check the nightly babysitter; manually re-run the workflow |
| `flywheel:pause` label ignored | Label name mismatch | Ensure the label is exactly `flywheel:pause` |
