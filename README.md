# PR Flywheel Template

> A practical, ship-today blueprint for an autonomous PR improvement loop using free / open / GHEC-native tools.

## What it is

This repository packages a PR flywheel that reviews a pull request, aggregates signals, decides whether to fix or wait, triggers remediation, and repeats until the PR is ready, blocked, or handed off.

It is designed for teams that want an auditable, bounded automation loop built on GitHub-native workflows plus optional Claude and Copilot integrations.

## How it works

The loop runs in six stages:

1. **Generate** – produce reviewer prompts and execution inputs for the current PR state.
2. **Dispatch Review** – run the reviewer swarm and collect structured findings.
3. **Aggregate** – combine reviewer findings, CI checks, human reviews, code scanning, and review thread state.
4. **Decide** – determine whether to fix, wait, hand off, block, or declare ready.
5. **Trigger Fix** – dispatch an autonomous fixer with the actionable issues.
6. **Loop** – persist state and repeat until a terminal condition is reached.

## Quick Start

Adopt the flywheel from a consumer repository with a small caller workflow:

```yaml
name: pr-flywheel
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]

jobs:
  flywheel:
    uses: your-org/pr-flywheel-template/.github/workflows/pr-flywheel.yml@main
    with:
      max_iterations: 5
      severity_floor: high
      enable_claude: true
      enable_copilot_agent: true
    secrets: inherit
```

Then:

1. Install the required GitHub App or token permissions.
2. Add the needed secrets in the consumer repo or org.
3. Configure branch protection so generated commits and checks behave as expected.
4. Open or update a PR to start the loop.

## Configuration

| Input | Default | Description |
| --- | --- | --- |
| `max_iterations` | `5` | Maximum flywheel rounds before handoff. |
| `severity_floor` | `high` | Lowest severity that becomes actionable. |
| `enable_claude` | `true` | Enables Claude-backed review or fix steps when configured. |
| `enable_copilot_agent` | `true` | Enables GitHub Copilot agent-based fix dispatch when configured. |

## Safety Rails

- Maximum of 5 iterations by default to prevent runaway loops.
- Severity-floor filtering so low-signal findings do not trigger unnecessary fixes.
- Forbidden paths support to keep automation away from sensitive files or directories.
- Kill-switch label support so humans can stop the loop from the PR UI.
- Audit artifacts persisted as state, signals, and decision outputs for traceability.
- Automated commits can include a `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>` trailer.

## Architecture

### Core modules

- `orchestrator/flywheel_controller.py` – state management, issue lifecycle, and next-step decisions.
- `orchestrator/signal_aggregator.py` – normalization of swarm findings, reviews, checks, code scanning, and thread signals.
- `orchestrator/models.py` – typed state and issue models.
- `orchestrator/severity_classifier.py` – severity threshold filtering for actionable work.

### Prompting and automation

- `prompts/*` – prompt assets used by reviewer and fixer steps.
- `.github/workflows/*` – reusable and local workflows that drive the loop.
- `task-helpers/*` – helper scripts and task-processing utilities used in adjacent automation flows.

## Prerequisites

- A GitHub App or token with permission to read PRs, reviews, checks, and post workflow-driven updates.
- An Anthropic API key if Claude-backed steps are enabled.
- Branch protection rules configured to allow the intended bot/app behavior and required checks.

## Development

Run orchestrator tests with:

```bash
uv run pytest orchestrator/tests/
```

Run a focused controller test file with:

```bash
uv run pytest orchestrator/tests/test_flywheel_controller.py -v
```

## Repository layout

```text
orchestrator/          Decision engine, aggregation, models, and tests
prompts/               Reviewer and fixer prompt assets
.github/workflows/     Reusable workflow entry points
task-helpers/          Supporting task execution utilities
```

## Status model

Issues generally move through:

- `open` – currently actionable or awaiting resolution
- `resolved` – cleared by reviewer absence, commit annotation, or dismissed review thread

Terminal loop states include `ready`, `waiting`, `blocked`, and `handoff`.
