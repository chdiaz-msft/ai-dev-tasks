# Autonomous PR Flywheel for GitHub Enterprise Cloud

A practical, ship-today blueprint for an autonomous PR improvement loop using free / open / GHEC-native tools. Mirrors patterns proven internally at Microsoft (`pr-monitor-and-fix`, Bebop `review-swarm`, Teams Flywheel Harness).

## Executive Summary

- **Six-stage orchestrator lifecycle.** Generate → Dispatch Review → Aggregate → Decide → Trigger Fix → Loop. Central template repo hosts a reusable GitHub Actions workflow; consumer repos opt in with a 10-line caller workflow and a config file.
- **Review Swarm with parallel evaluators.** Independent correctness and security reviewers run in parallel, each producing structured findings with severity, issue description, and suggested fix.
- **Stateful Loop Engine.** Tracks issues by ID across rounds — open, resolved, escalated — with formal termination conditions (success, handoff, blocked, waiting).
- **Copilot Code Review + Claude Code Action.** Free native reviewer for breadth, Claude Code Action for deep fixes — both run inside Actions, no external orchestration.
- **Python orchestrator inside Actions.** `flywheel_controller.py` manages the loop engine, reads swarm findings via PyGithub, classifies severity, decides "fix vs. escalate", caps at 5 iterations.
- **Branch protection forces human merge.** Required status `flywheel/merge-ready` + 1 human approval = autonomy on fixes, human ownership of merge.

---

## 1. Orchestrator Lifecycle

The control loop follows a six-stage cycle. Each PR event enters the loop at **Generate** and exits only when a termination condition is met.

```
┌──────────┐     ┌──────────────────┐     ┌─────────────┐
│ Generate │────▶│ Dispatch Review  │────▶│  Aggregate   │
└──────────┘     └──────────────────┘     └──────┬──────┘
      ▲                                         │
      │                                         ▼
┌─────┴──────┐                            ┌──────────┐
│    Loop    │◀───────────────────────────│  Decide   │
└─────┬──────┘                            └──────┬───┘
      │          ┌─────────────┐                 │
      │          │ Trigger Fix │◀────────────────┘
      │          └──────┬──────┘          (if fixable)
      │                 │
      └─────────────────┘
```

### Stage Definitions

| # | Stage | Owner module | Description |
|---|-------|-------------|-------------|
| 1 | **Generate** | `flywheel_controller.py` | A PR event (open, push, comment, check complete) enters the loop. The controller loads or creates the loop state file and increments the round counter. |
| 2 | **Dispatch Review** | `review_swarm.py` | Fan out to the Review Swarm (§2). Each independent reviewer receives the PR diff and produces structured findings in parallel. |
| 3 | **Aggregate** | `signal_aggregator.py` | Collect all reviewer findings plus CI checks, security scans, and human comments into a single normalized signal set. De-duplicate across reviewers. Assign a stable `issue_id` to each finding. |
| 4 | **Decide** | `flywheel_controller.py` | Evaluate the aggregated signals against the severity floor. Determine next action: fix, escalate, wait, or terminate. Update the loop state with new/resolved issues. |
| 5 | **Trigger Fix** | `fix_dispatcher.py` | If the decision is "fix", dispatch the fix agent (Claude Code Action or Copilot Coding Agent) with the actionable items. The agent commits fixes and replies to review threads. |
| 6 | **Loop** | `flywheel_controller.py` | The fix commit retriggers the workflow. Control returns to **Generate** for the next round. |

### Key Rules (non-negotiable)

- Max 5 iterations (configurable via `max_iterations`)
- 5-minute minimum poll interval between rounds (enforced by `flywheel_controller.py` via `last_round_timestamp` in loop state)
- Always new commits — never amend, never force-push
- Every auto-commit includes a `Co-authored-by` trailer

### Exit Conditions

| State | Condition | Action |
|-------|-----------|--------|
| **SUCCESS** | All checks green + no open issues above severity floor | Post `flywheel/merge-ready` check; ping reviewer |
| **WAITING** | All checks green + unresolved human threads remain | Post summary comment; wait for human |
| **HANDOFF** | Max iterations reached | Post summary of remaining issues; assign to human |
| **BLOCKED** | Human submitted "Request Changes" rejection | Stop all automation; post acknowledgment |

---

## 2. Review Swarm (Parallel Reviewers)

The Review Swarm is a set of independent evaluators that run in parallel during the **Dispatch Review** stage (§1). Each reviewer focuses on a single concern, receives the PR diff, and produces structured findings.

### Reviewer Roles (initial set)

| Role | Focus | Example signals |
|------|-------|----------------|
| **Correctness** | Logic bugs, runtime errors, wrong behavior, broken contracts | Off-by-one, null deref, wrong return type, race condition |
| **Security** | Injection, secrets, auth flaws, unsafe deserialization | SQL injection, hardcoded API key, missing auth check, SSRF |

> **Future additions:** Test Coverage reviewer (missing tests, weak assertions, untested paths) and Style / Maintainability reviewer (naming, complexity, readability). Add these once the core loop is proven.

### Structured Output Schema

Every reviewer produces a list of findings. Each finding follows this schema:

```json
{
  "issue_id": "correctness-001",
  "reviewer": "correctness",
  "severity": "high",
  "issue": "Division by zero when `batch_size` is 0 on line 42 of processor.py",
  "suggested_fix": "Add a guard: `if batch_size == 0: raise ValueError('batch_size must be > 0')`",
  "file": "src/processor.py",
  "line": 42
}
```

| Field | Type | Description |
|-------|------|-------------|
| `issue_id` | `string` | Deterministic content-hash ID: `{reviewer}-{sha256(file+line+issue)[:8]}`. Stable across rounds even if finding order changes. Used by the Loop Engine to track resolution. |
| `reviewer` | `string` | Which reviewer produced this finding. |
| `severity` | `enum` | `critical \| high \| medium \| low \| info` — same scale used throughout the system. |
| `issue` | `string` | Human-readable description of the problem. |
| `suggested_fix` | `string` | Concrete fix suggestion. May be empty if the reviewer cannot suggest one. |
| `file` | `string` | File path relative to repo root. |
| `line` | `int \| null` | Line number, if applicable. |

### Dispatch & Collection

1. **Dispatch** — `review_swarm.py` launches all reviewers in parallel (e.g., concurrent subprocess calls or parallel API requests). Each reviewer gets:
   - The PR diff (`git diff main...HEAD`)
   - The repo's review doctrine (`.github/copilot-instructions.md` or `REVIEW.md`)
   - The current loop state (so reviewers can see what was already flagged/fixed)

2. **Collection** — All reviewer outputs are gathered into a single `swarm_findings.json` array. The aggregator de-duplicates findings that overlap across reviewers (same file + line + similar issue text).

3. **Timeout** — Each reviewer has a 2-minute timeout. If a reviewer fails or times out, the loop continues with findings from the remaining reviewers. A warning is logged.

### Reviewer Prompt Templates

Each reviewer is driven by a focused system prompt. Stored in `prompts/reviewers/`:

```
prompts/
├── reviewers/
│   ├── correctness.md    # "You are a correctness reviewer. Focus ONLY on..."
│   └── security.md       # "You are a security reviewer. Focus ONLY on..."
├── REVIEW.md
└── claude-fix-prompt.md
```

---

## 3. Loop Engine (Stateful)

The Loop Engine maintains state across rounds so the orchestrator knows what has been found, what has been fixed, and when to stop.

### State File: `loop-state.json`

Persisted via GitHub Actions cache with `restore-keys` prefix matching for reliable cross-round retrieval. The cache restore key uses prefix `flywheel-state-pr-N-` so any prior round's save (e.g., `flywheel-state-pr-N-round-2`) is found automatically. Cache misses (first round) are handled explicitly by creating a fresh state object.

```json
{
  "pr_number": 123,
  "current_round": 2,
  "max_rounds": 5,
  "last_round_timestamp": "2025-06-15T14:30:00+00:00",
  "issues": {
    "correctness-001": {
      "reviewer": "correctness",
      "severity": "high",
      "issue": "Division by zero when batch_size is 0",
      "file": "src/processor.py",
      "line": 42,
      "found_in_round": 1,
      "status": "resolved",
      "resolved_in_round": 2,
      "resolution": "Fixed in commit abc1234"
    },
    "security-001": {
      "reviewer": "security",
      "severity": "critical",
      "issue": "Hardcoded API key in config.py",
      "file": "src/config.py",
      "line": 7,
      "found_in_round": 1,
      "status": "open",
      "resolved_in_round": null,
      "resolution": null
    }
  },
  "termination": null
}
```

### Tracked Fields

| Field | Type | Description |
|-------|------|-------------|
| `current_round` | `int` | Incremented at the start of each Generate stage. |
| `max_rounds` | `int` | Hard cap from `max_iterations` input. |
| `issues` | `dict[issue_id, Issue]` | All issues ever discovered, keyed by stable ID. |
| `issues[].status` | `enum` | `open \| resolved \| wont_fix \| escalated` |
| `issues[].found_in_round` | `int` | Round when the issue was first detected. |
| `issues[].resolved_in_round` | `int \| null` | Round when the issue was confirmed fixed. |
| `termination` | `string \| null` | Set when the loop exits: `success`, `handoff`, `blocked`, or `waiting`. |
| `last_round_timestamp` | `string \| null` | ISO 8601 UTC timestamp of the last round start. Used to enforce the 5-minute minimum poll interval between rounds. |

### Derived Views

The controller computes these from the issues dict each round:

```python
open_issues     = {k: v for k, v in issues.items() if v["status"] == "open"}
resolved_issues = {k: v for k, v in issues.items() if v["status"] == "resolved"}
actionable      = {k: v for k, v in open_issues.items()
                   if SEV_ORDER[v["severity"]] >= SEV_ORDER[severity_floor]}
```

### Termination Conditions

| Condition | Result | Loop continues? |
|-----------|--------|----------------|
| `len(actionable) == 0` and all checks green | **SUCCESS** | No |
| `len(actionable) == 0` but human threads open | **WAITING** | No |
| `current_round >= max_rounds` | **HANDOFF** | No |
| Human submitted "Request Changes" | **BLOCKED** | No |
| `len(actionable) > 0` and `current_round < max_rounds` | Fix round | **Yes** |

### Issue Resolution Detection

An issue is marked `resolved` when:
1. A subsequent round's swarm review no longer flags it (same `file` + `line` range + similar issue text), OR
2. The fix agent explicitly marks it resolved in its commit message (`[resolves: correctness-001]`), OR
3. A human dismisses the review thread.

---

## 4. Recommended Tool Stack (free / GHEC-native only)

| Layer | Tool | Why |
|---|---|---|
| AI reviewer | **GitHub Copilot Code Review** (auto-triggered via Repository Ruleset) | Native, free with Copilot Business/Enterprise; reads `.github/copilot-instructions.md` and `.github/instructions/*.instructions.md`; supports CodeQL/ESLint/PMD ruleset integration |
| Deep reviewer + fixer | **Claude Code GitHub Action** (`anthropics/claude-code-action`) | Runs in Actions, supports `REVIEW.md`, can both review and push fixes |
| Backup fixer | **GitHub Copilot Coding Agent** (assign issue/PR to `@copilot`) | First-party, repo-scoped, runs in isolated firewalled Actions env |
| CI / linting | GitHub Actions matrix + `pre-commit`, `ruff`, `eslint`, `pytest` | Deterministic gate — autonomy depends on strong tests + strict linting + fast CI |
| Security scanning | **CodeQL**, **Dependabot**, **Trivy**, **Bandit**, **Semgrep OSS** | All free, all produce SARIF that lands in the Security tab |
| Orchestrator | **Python 3.11 + PyGithub** (or `githubkit`) in a workflow step | Easy to read, easy to test locally |
| Multi-repo distribution | **Reusable workflows** (`workflow_call`) + **org workflow templates** | One source of truth, 10-line opt-in |
| Auth for write-back | **GitHub App** with `contents:write`, `pull-requests:write`, `checks:write` | Least-privilege, auditable, no PATs to rotate |

> **Microsoft-internal note:** Claude Code cannot be used with ANY customer data. For product/code repos this flywheel is fine; if PRs contain customer data, route through GitHub Copilot using Azure OpenAI instead.

---

## 5. Repository Layout — the "template" repo

```
pr-flywheel-template/
├── .github/
│   ├── workflows/
│   │   ├── flywheel.yml              # Reusable workflow (workflow_call)
│   │   └── flywheel-nightly.yml      # Babysitter cron (catches stuck PRs)
│   └── workflow-templates/
│       ├── flywheel-caller.yml       # Org-level template for consumers
│       └── flywheel-caller.properties.json
├── orchestrator/
│   ├── flywheel_controller.py        # Main control loop + loop engine
│   ├── review_swarm.py               # Dispatch parallel reviewers, collect findings
│   ├── signal_aggregator.py          # Normalize swarm + CI + human signals
│   ├── severity_classifier.py        # critical/high/medium/low/info
│   ├── fix_dispatcher.py             # Pick Claude vs Copilot agent
│   └── requirements.txt
├── prompts/
│   ├── reviewers/
│   │   ├── correctness.md            # Correctness reviewer system prompt
│   │   └── security.md               # Security reviewer system prompt
│   ├── REVIEW.md                     # Default review doctrine
│   ├── claude-fix-prompt.md          # Fix prompt template
│   └── copilot-instructions.md       # Default repo-level instructions
├── state/
│   └── loop-state.json               # Persisted loop state (also stored as artifact)
└── README.md
```

---

## 6. The Reusable Workflow

`.github/workflows/flywheel.yml` in the template repo:

```yaml
name: PR Flywheel
on:
  workflow_call:
    inputs:
      max_iterations:   { type: number, default: 5 }
      severity_floor:   { type: string, default: "high" }   # only auto-fix >= this
      enable_claude:    { type: boolean, default: true }
      enable_copilot_agent: { type: boolean, default: false }
    secrets:
      ANTHROPIC_API_KEY: { required: false }
      FLYWHEEL_APP_ID:   { required: true }
      FLYWHEEL_APP_KEY:  { required: true }

permissions:
  contents: write
  pull-requests: write
  checks: write
  issues: write
  security-events: read

concurrency:
  group: flywheel-${{ steps.pr-info.outputs.pr_number }}
  cancel-in-progress: true   # cancel stale runs reviewing old diffs to prevent obsolete fixes

jobs:
  flywheel:
    if: |
      github.event.pull_request.head.repo.full_name == github.repository &&
      (github.event_name != 'check_suite' || (
        github.event.check_suite.app.slug != 'flywheel-bot' &&
        github.event.check_suite.check_runs[0].name != 'flywheel/merge-ready'
      )) &&
      (github.event.pull_request.draft == false || steps.pr-info.outputs.draft == 'false')
    runs-on: ubuntu-latest
    steps:
      - name: Generate GitHub App token (scoped)
        id: app-token
        uses: actions/create-github-app-token@v2
        with:
          app-id:      ${{ secrets.FLYWHEEL_APP_ID }}
          private-key: ${{ secrets.FLYWHEEL_APP_KEY }}

      - name: Normalize event / resolve PR info
        id: pr-info
        env:
          GH_TOKEN: ${{ steps.app-token.outputs.token }}
        run: |
          # Normalize across pull_request, pull_request_review, check_suite events
          if [ -n "${{ github.event.pull_request.number }}" ]; then
            echo "pr_number=${{ github.event.pull_request.number }}" >> "$GITHUB_OUTPUT"
            echo "head_ref=${{ github.event.pull_request.head.ref }}" >> "$GITHUB_OUTPUT"
            echo "head_sha=${{ github.event.pull_request.head.sha }}" >> "$GITHUB_OUTPUT"
            echo "draft=${{ github.event.pull_request.draft }}" >> "$GITHUB_OUTPUT"
          else
            # check_suite: look up associated PRs via API
            PR_DATA=$(gh api repos/${{ github.repository }}/commits/${{ github.event.check_suite.head_sha }}/pulls --jq '.[0]')
            echo "pr_number=$(echo "$PR_DATA" | jq -r '.number')" >> "$GITHUB_OUTPUT"
            echo "head_ref=$(echo "$PR_DATA" | jq -r '.head.ref')" >> "$GITHUB_OUTPUT"
            echo "head_sha=$(echo "$PR_DATA" | jq -r '.head.sha')" >> "$GITHUB_OUTPUT"
            echo "draft=$(echo "$PR_DATA" | jq -r '.draft')" >> "$GITHUB_OUTPUT"
          fi

      - name: Checkout PR head
        uses: actions/checkout@v4
        with:
          ref: ${{ steps.pr-info.outputs.head_ref }}
          token: ${{ steps.app-token.outputs.token }}
          fetch-depth: 0

      - name: Checkout template repo (orchestrator + prompts)
        uses: actions/checkout@v4
        with:
          repository: org/pr-flywheel-template
          ref: v1
          path: _flywheel
          token: ${{ steps.app-token.outputs.token }}

      - name: Set up Python
        uses: actions/setup-python@v5
        with: { python-version: '3.11' }

      - name: Install orchestrator
        run: pip install -r _flywheel/orchestrator/requirements.txt

      - name: Restore loop state
        id: restore-state
        uses: actions/cache/restore@v4
        with:
          path: state/loop-state.json
          key: flywheel-state-pr-${{ steps.pr-info.outputs.pr_number }}-round-99999
          restore-keys: |
            flywheel-state-pr-${{ steps.pr-info.outputs.pr_number }}-round-
            flywheel-state-pr-${{ steps.pr-info.outputs.pr_number }}

      - name: Dispatch review swarm
        id: swarm
        env:
          GH_TOKEN: ${{ steps.app-token.outputs.token }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          PR_NUMBER: ${{ steps.pr-info.outputs.pr_number }}
          REPO: ${{ github.repository }}
        run: python _flywheel/orchestrator/review_swarm.py > swarm_result.json
        # Output now contains { "findings": [...], "reviewer_coverage": {...} }

      - name: Aggregate signals
        id: signals
        env:
          GH_TOKEN: ${{ steps.app-token.outputs.token }}
          PR_NUMBER: ${{ steps.pr-info.outputs.pr_number }}
          REPO: ${{ github.repository }}
        run: |
          python _flywheel/orchestrator/signal_aggregator.py \
            --swarm-findings swarm_result.json > signals.json

      - name: Classify & decide (loop engine)
        id: decide
        env: { GH_TOKEN: ${{ steps.app-token.outputs.token }} }
        run: |
          python _flywheel/orchestrator/flywheel_controller.py \
            --signals signals.json \
            --max-iter ${{ inputs.max_iterations }} \
            --severity-floor ${{ inputs.severity_floor }} \
            --out decision.json

      - name: Save loop state
        uses: actions/cache/save@v4
        with:
          path: state/loop-state.json
          key: flywheel-state-pr-${{ steps.pr-info.outputs.pr_number }}-round-${{ steps.decide.outputs.iteration }}

      - name: Run Claude Code fix (if dispatched)
        if: fromJson(steps.decide.outputs.dispatch).agent == 'claude' && inputs.enable_claude
        uses: anthropics/claude-code-action@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          github_token:      ${{ steps.app-token.outputs.token }}
          mode: agent
          # Use inline prompt instead of prompt_file — avoids reliance on template
          # interpolation ({{...}}) which the action does not support. The agent
          # reads decision.json at runtime to discover its fix items.
          prompt: |
            You are the FLYWHEEL FIX AGENT. Read `decision.json` in the repo root
            for the list of issues you must fix. Follow the rules in
            `_flywheel/prompts/claude-fix-prompt.md`. Do NOT commit — the workflow
            handles commits after you exit.
          allowed_tools: "Bash,Edit,Read,Write,Grep"
          max_turns: 30
          # Note: claude-code-action does NOT auto-commit. It modifies files in
          # the working directory; the workflow's explicit "Commit + push" step
          # detects and commits those changes via `git diff`.

      - name: Validate changed paths against allowlist (blocking gate)
        id: validate-changes
        if: steps.decide.outputs.has_changes == 'true'
        run: |
          # Hard enforcement: FAIL the job if forbidden paths are touched
          FORBIDDEN=$(git diff --name-only HEAD | grep -E '^\.(github/workflows/|.*secrets)' || true)
          if [ -n "$FORBIDDEN" ]; then
            echo "::error::Fix agent modified forbidden paths: $FORBIDDEN"
            exit 1
          fi

      - name: Commit + push (with Co-authored-by)
        if: steps.decide.outputs.has_changes == 'true'
        run: |
          git config user.name  "flywheel-bot[bot]"
          git config user.email "flywheel-bot@users.noreply.github.com"
          # Only commit if there are actual file changes (not just decision intent)
          if ! git diff --quiet || ! git diff --cached --quiet; then
            git add -A
            git commit -m "fix: flywheel iteration ${{ steps.decide.outputs.iteration }}

          Co-authored-by: Claude <noreply@anthropic.com>"
            git push   # never force-push
          else
            echo "No actual file changes to commit"
          fi

      - name: Publish merge-ready check
        if: steps.decide.outputs.state == 'ready'
        uses: LouisBrunner/checks-action@v2
        with:
          token: ${{ steps.app-token.outputs.token }}
          name: flywheel/merge-ready
          sha: ${{ steps.pr-info.outputs.head_sha }}   # pin to current HEAD so check invalidates on new pushes
          conclusion: success
          output: |
            { "summary": "All severity-${{ inputs.severity_floor }}+ issues resolved across ${{ steps.decide.outputs.iteration }} iteration(s). Human review required to merge." }
```

**Why this shape works:**
- `workflow_call` ⇒ every consumer repo is one tiny caller workflow.
- GitHub App token (not `GITHUB_TOKEN`) ⇒ pushes trigger downstream workflows. The default `GITHUB_TOKEN` does NOT, which breaks the loop.
- `concurrency.cancel-in-progress: true` ⇒ stale runs reviewing old diffs are cancelled; the next push retriggers a fresh round anyway (spec rule: always new commits, never amend).
- All commits are new (never amend) and never force-pushed.

---

## 7. The Consumer Repo (10-line opt-in)

In every repo that wants the flywheel, drop `.github/workflows/flywheel.yml`:

```yaml
name: PR Flywheel
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]
  pull_request_review:
    types: [submitted]
  pull_request_review_comment:
    types: [created]
  check_suite:
    types: [completed]

jobs:
  flywheel:
    uses: org/pr-flywheel-template/.github/workflows/flywheel.yml@v1
    with:
      max_iterations: 5
      severity_floor: high
      enable_claude: true
    secrets:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
      FLYWHEEL_APP_ID:   ${{ secrets.FLYWHEEL_APP_ID }}
      FLYWHEEL_APP_KEY:  ${{ secrets.FLYWHEEL_APP_KEY }}
```

Plus per-repo doctrine — `.github/copilot-instructions.md` is auto-picked-up by Copilot Code Review; `REVIEW.md` by Claude Code:

```markdown
# Copilot / Claude review doctrine for THIS repo
- Architecture: layered (api / domain / infra). Flag cross-layer imports.
- Tests: every new public function must have a pytest unit test.
- Security: never log secrets; reject hardcoded API keys.
- Style: ruff + black; do not comment on formatting (pre-commit handles it).
- Severity:
  - high  = correctness bug, security, data loss
  - med   = perf, missing tests, weak error handling
  - low   = naming, docs, refactor suggestions  (do NOT auto-fix)
```

---

## 8. The Python Orchestrator

### `orchestrator/review_swarm.py` — parallel reviewer dispatch

```python
"""Dispatch parallel reviewers and collect structured findings."""
import json, os, subprocess, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REVIEWERS = ["correctness", "security"]  # extend later: "test_coverage", "style"
TIMEOUT_SECONDS = 120

def run_reviewer(reviewer: str, diff: str, doctrine: str, loop_state: dict) -> list[dict]:
    """Run a single reviewer and return its findings."""
    prompt_path = Path(f"prompts/reviewers/{reviewer}.md")
    prompt = prompt_path.read_text()

    # Build the reviewer input
    reviewer_input = json.dumps({
        "reviewer": reviewer,
        "diff": diff,
        "doctrine": doctrine,
        "prior_issues": {
            k: v for k, v in loop_state.get("issues", {}).items()
            if v["reviewer"] == reviewer
        },
    })

    # Call the AI reviewer (Claude API, subprocess, etc.)
    # Returns a list of findings matching the structured schema
    result = _call_reviewer_api(prompt, reviewer_input)
    return result

def dispatch_swarm(diff: str, doctrine: str, loop_state: dict) -> dict:
    """Fan out all reviewers in parallel, collect findings and coverage metadata."""
    all_findings: list[dict] = []
    completed_reviewers: list[str] = []
    failed_reviewers: list[dict] = []

    with ThreadPoolExecutor(max_workers=len(REVIEWERS)) as pool:
        futures = {
            pool.submit(run_reviewer, r, diff, doctrine, loop_state): r
            for r in REVIEWERS
        }
        for future in as_completed(futures, timeout=TIMEOUT_SECONDS):
            reviewer = futures[future]
            try:
                findings = future.result()
                all_findings.extend(findings)
                completed_reviewers.append(reviewer)
            except Exception as e:
                print(f"WARNING: {reviewer} reviewer failed: {e}", file=sys.stderr)
                failed_reviewers.append({"reviewer": reviewer, "error": str(e)})

    return {
        "findings": all_findings,
        "reviewer_coverage": {
            "expected": REVIEWERS,
            "completed": completed_reviewers,
            "failed": failed_reviewers,
        },
    }
```

### `orchestrator/signal_aggregator.py` — normalized signal collection

Pulls swarm findings, CI checks, human comments, and security scans into one blob:

```python
"""Collect all flywheel signals into signals.json."""
import json, os, sys
from github import Github

gh   = Github(os.environ["GH_TOKEN"])
repo = gh.get_repo(os.environ["REPO"])
pr   = repo.get_pull(int(os.environ["PR_NUMBER"]))

signals = {
    "pr_number": pr.number,
    "head_sha":  pr.head.sha,
    "iteration": _count_label(pr, prefix="flywheel:iter-"),
    "swarm_findings": [],   # populated by review_swarm.py
    "reviewer_coverage": {},  # populated by review_swarm.py (completed/failed reviewers)
    "checks":    [],
    "ai_reviews": [],
    "human_reviews": [],
    "unresolved_threads": [],  # populated via GraphQL below
    "security": [],
}

# 1. Check runs (CI, lint, scans)
for cr in repo.get_commit(pr.head.sha).get_check_runs():
    signals["checks"].append({
        "name": cr.name,
        "conclusion": cr.conclusion,
        "details_url": cr.details_url,
        "output_summary": (cr.output.summary or "")[:2000],
    })

# 2. Review comments — distinguish bot vs human
for c in pr.get_review_comments():
    bucket = "ai_reviews" if c.user.type == "Bot" or "copilot" in c.user.login.lower() \
                          else "human_reviews"
    signals[bucket].append({
        "id": c.id,
        "author": c.user.login,
        "body": c.body,
        "path": c.path,
        "line": c.line,
        "in_reply_to": c.in_reply_to_id,
    })

# 3. Top-level reviews (Approve / Request changes)
for r in pr.get_reviews():
    signals["human_reviews"].append({
        "id": r.id, "state": r.state, "author": r.user.login, "body": r.body or "",
    })

# 4. Code scanning alerts (CodeQL / Dependabot) — use REST API directly
#    PyGithub does not reliably expose code scanning; use subprocess + gh CLI.
import subprocess as _sp
try:
    _alerts_raw = _sp.run(
        ["gh", "api", "--paginate",
         f"repos/{os.environ['REPO']}/code-scanning/alerts",
         "-q", f'[.[] | select(.state=="open" and .most_recent_instance.ref=="refs/heads/{pr.head.ref}")]'],
        capture_output=True, text=True, check=True,
    ).stdout
    for a in json.loads(_alerts_raw or "[]"):
        signals["security"].append({
            "rule": a["rule"]["id"],
            "severity": a["rule"].get("security_severity_level", "unknown"),
            "path": a["most_recent_instance"]["location"]["path"],
        })
except _sp.CalledProcessError as e:
    # 403 = permissions not granted, 404 = code scanning not configured
    print(f"WARNING: code scanning alerts unavailable: {e.stderr}", file=sys.stderr)

# 5. Unresolved review threads (requires GraphQL — REST cannot expose isResolved)
_graphql_query = """
query($owner: String!, $repo: String!, $pr: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100) {
        nodes {
          isResolved
          comments(first: 1) {
            nodes { body, path, line: originalLine, author { login } }
          }
        }
      }
    }
  }
}
"""
try:
    _owner, _name = os.environ["REPO"].split("/")
    _gql_raw = _sp.run(
        ["gh", "api", "graphql",
         "-F", f"owner={_owner}", "-F", f"repo={_name}",
         "-F", f"pr={pr.number}", "-f", f"query={_graphql_query}",
         "--jq", '.data.repository.pullRequest.reviewThreads.nodes'],
        capture_output=True, text=True, check=True,
    ).stdout
    for thread in json.loads(_gql_raw or "[]"):
        if not thread["isResolved"] and thread["comments"]["nodes"]:
            c = thread["comments"]["nodes"][0]
            signals["unresolved_threads"].append({
                "path": c.get("path"), "line": c.get("line"),
                "body": c.get("body", "")[:500],
                "author": c.get("author", {}).get("login", "unknown"),
            })
except _sp.CalledProcessError as e:
    print(f"WARNING: GraphQL review threads query failed: {e.stderr}", file=sys.stderr)

json.dump(signals, sys.stdout, indent=2)
```

### `orchestrator/flywheel_controller.py` — decision engine + loop state

```python
"""Classify signals, manage loop state, decide next action."""
import argparse, json, os, sys
from datetime import datetime, timezone
from pathlib import Path

SEV_ORDER = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
STATE_FILE = Path("state/loop-state.json")
MIN_POLL_INTERVAL_SECONDS = 300  # 5-minute minimum between rounds

def load_state(pr_number: int, max_rounds: int) -> dict:
    """Load existing loop state or create a fresh one."""
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text())
    else:
        state = {
            "pr_number": pr_number,
            "current_round": 0,
            "max_rounds": max_rounds,
            "issues": {},
            "termination": None,
            "last_round_timestamp": None,
        }
    state["current_round"] += 1
    state["last_round_timestamp"] = datetime.now(timezone.utc).isoformat()
    return state

def merge_findings(state: dict, findings: list[dict]) -> None:
    """Merge new swarm findings into the loop state's issue tracker."""
    seen_ids = set()
    for f in findings:
        issue_id = f["issue_id"]
        seen_ids.add(issue_id)
        if issue_id not in state["issues"]:
            state["issues"][issue_id] = {
                **f,
                "found_in_round": state["current_round"],
                "status": "open",
                "resolved_in_round": None,
                "resolution": None,
            }

    # Mark issues as resolved if no longer flagged by the same reviewer
    for issue_id, issue in state["issues"].items():
        if issue["status"] == "open" and issue_id not in seen_ids:
            if issue["reviewer"] in {f["reviewer"] for f in findings}:
                issue["status"] = "resolved"
                issue["resolved_in_round"] = state["current_round"]
                issue["resolution"] = "No longer flagged by reviewer"

def decide(state: dict, severity_floor: str, signals: dict) -> dict:
    """Evaluate loop state and decide next action."""
    floor = SEV_ORDER[severity_floor]

    open_issues = {k: v for k, v in state["issues"].items() if v["status"] == "open"}
    actionable = {k: v for k, v in open_issues.items()
                  if SEV_ORDER.get(v["severity"], 0) >= floor}

    if state["current_round"] >= state["max_rounds"]:
        state["termination"] = "handoff"
        return {"state": "handoff", "reason": "max_iterations_reached",
                "open_issues": list(open_issues.keys())}

    # Check for human rejection — read from signals, not state["issues"]
    if any(r.get("state") == "CHANGES_REQUESTED" for r in signals.get("human_reviews", [])):
        state["termination"] = "blocked"
        return {"state": "blocked", "reason": "human_rejection"}

    if not actionable:
        # Verify all required checks are green before declaring success
        checks = signals.get("checks", [])
        required = signals.get("required_checks", [])
        failing = [
            c["name"] for c in checks
            if (not required or c["name"] in required)
            and c.get("conclusion") != "success"
        ]
        if failing:
            state["termination"] = "waiting"
            return {"state": "waiting", "reason": "checks_not_green",
                    "failing_checks": failing, "iteration": state["current_round"]}

        # Verify all reviewers completed — don't declare success with partial coverage
        coverage = signals.get("reviewer_coverage", {})
        failed_reviewers = coverage.get("failed", [])
        if failed_reviewers:
            state["termination"] = "waiting"
            return {"state": "waiting", "reason": "incomplete_review_coverage",
                    "failed_reviewers": [r["reviewer"] for r in failed_reviewers],
                    "iteration": state["current_round"]}

        state["termination"] = "success"
        return {"state": "ready", "iteration": state["current_round"]}

    return {
        "state": "fixing",
        "iteration": state["current_round"],
        "dispatch": {"agent": "claude", "items": list(actionable.values())[:10]},
        "has_changes": True,
    }

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--signals", required=True)
    p.add_argument("--max-iter", type=int, default=5)
    p.add_argument("--severity-floor", default="high")
    p.add_argument("--out", required=True)
    args = p.parse_args()

    sig = json.loads(Path(args.signals).read_text())
    state = load_state(sig["pr_number"], args.max_iter)

    # Enforce 5-minute minimum poll interval between rounds
    prev_ts = state.get("last_round_timestamp")
    if prev_ts and state["current_round"] > 1:
        prev_time = datetime.fromisoformat(prev_ts)
        elapsed = (datetime.now(timezone.utc) - prev_time).total_seconds()
        if elapsed < MIN_POLL_INTERVAL_SECONDS:
            wait_remaining = int(MIN_POLL_INTERVAL_SECONDS - elapsed)
            state["current_round"] -= 1  # don't count this as a real round
            decision = {"state": "waiting", "reason": "poll_interval_not_elapsed",
                        "retry_after_seconds": wait_remaining,
                        "iteration": state["current_round"]}
            STATE_FILE.parent.mkdir(exist_ok=True)
            STATE_FILE.write_text(json.dumps(state, indent=2))
            Path(args.out).write_text(json.dumps(decision, indent=2))
            gho = open(os.environ["GITHUB_OUTPUT"], "a")
            print(f"state=waiting", file=gho)
            print(f"iteration={state['current_round']}", file=gho)
            print(f"dispatch={{}}", file=gho)
            print(f"has_changes=false", file=gho)
            return

    # Check for human rejection before proceeding
    for review in sig.get("human_reviews", []):
        if review.get("state") == "CHANGES_REQUESTED":
            state["termination"] = "blocked"

    # Merge swarm findings into loop state
    merge_findings(state, sig.get("swarm_findings", []))

    decision = decide(state, args.severity_floor, signals=sig)

    # Persist loop state
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))

    # Write decision
    Path(args.out).write_text(json.dumps(decision, indent=2))
    gho = open(os.environ["GITHUB_OUTPUT"], "a")
    print(f"state={decision['state']}", file=gho)
    print(f"iteration={decision.get('iteration',0)}", file=gho)
    print(f"dispatch={json.dumps(decision.get('dispatch',{}))}", file=gho)
    print(f"has_changes={str(decision.get('has_changes', False)).lower()}", file=gho)

if __name__ == "__main__":
    main()
```

`prompts/claude-fix-prompt.md` — the fix agent's marching orders (referenced by the inline prompt; the agent reads `decision.json` at runtime):

```markdown
You are the FLYWHEEL FIX AGENT for this repo.
Read `decision.json` in the working directory to find the list of issues you must fix.
The issues are under `dispatch.items` — each has an `issue_id`, `file`, `line`, `issue`,
and `suggested_fix`.

Rules (NON-NEGOTIABLE):
1. Touch only files mentioned in the issue items, or files they directly depend on.
2. Run the repo's `make test` / `pytest` / `npm test` and ensure green BEFORE you stop.
3. For each item you fix, reply to the original review thread with:
   "[flywheel] Fixed in commit <sha>: <one-line explanation>"
4. Include `[resolves: <issue_id>]` in your commit message for each resolved issue
   so the Loop Engine can track resolution (e.g. `[resolves: correctness-001]`).
5. If an item is ambiguous or risky (e.g. requires API change), DO NOT fix it.
   Instead reply: "[flywheel] Needs human: <why>" and skip it.
6. Never edit `.github/workflows/**` or `**/secrets/**`.
7. Do NOT commit or push — the workflow handles commits after you exit.
8. Use Conventional Commits style for the commit message the workflow will use.
```

---

## 9. Branch Protection — the "Human Merge" Gate

In each consumer repo, configure Repository Rules (or classic branch protection):

| Rule | Setting |
|---|---|
| Required status checks | `flywheel/merge-ready`, `ci`, `codeql` |
| Required reviews | 1 human approval (Copilot Code Review approvals don't count) |
| Dismiss stale approvals on push | **ON** — use a Repository Ruleset with path conditions so only changes to sensitive paths (`.github/`, `infra/`) dismiss stale approvals, while `src/` and `tests/` changes do not. The flywheel requests a fresh human review after its final fix round by posting a comment tagging the original approver. |
| Restrict who can push | Allow `flywheel-bot[bot]` GitHub App |
| Auto-merge | Enabled — fires when status checks pass AND a human approves |

Agent has full autonomy on fixes; human still owns the merge click.

> **Note on merge-ready check staleness:** The `flywheel/merge-ready` check is pinned to the PR's HEAD SHA at the time it is published. If new commits are pushed after the check is posted, the check becomes stale (attached to an old SHA) and branch protection will require re-evaluation. This is the desired behavior — any new code must go through the flywheel loop again before the PR is mergeable.

---

## 10. Multi-Repo Distribution

Use both mechanisms:

**a) Reusable workflow** — consumers reference `org/pr-flywheel-template/.github/workflows/flywheel.yml@v1`. Tag the template repo with semver so consumers pin a version.

**b) Org workflow template** — put the caller stub in `<your-org>/.github/workflow-templates/flywheel-caller.yml`. New repos see it in the "Add workflow" UI and one click installs it. Pair with `flywheel-caller.properties.json` for name/description/category.

Optionally, generate consumer wiring with a CLI:

```bash
gh repo clone org/new-service && cd new-service
curl -sSL https://raw.githubusercontent.com/org/pr-flywheel-template/main/install.sh | bash
# script copies caller workflow, copilot-instructions.md, REVIEW.md skeleton, configures secrets
```

---

## 11. Safety Rails

- **Hard iteration cap.** Default 5. Hitting cap = automatic human handoff with summary comment.
- **Severity floor.** Default `high`. Low-severity items surfaced as comments but never auto-fixed.
- **Forbidden paths.** Fix agent cannot edit workflows, secrets, or IaC entry points. Enforced in the prompt and via path-allowlist check before `git push`.
- **Kill switch.** Adding label `flywheel:pause` causes workflow to exit early.
- **Audit trail.** Every iteration writes `decision.json` as a workflow artifact. Run logs retained per org policy.
- **Co-authored-by.** Every auto-commit tags Claude/Copilot as co-author so AI-generated code share is measurable.

---

## 12. Rollout Plan

| Week | Step |
|---|---|
| 1 | Template repo + 1 pilot. Install GitHub App, ship to low-risk repo. Severity floor = `critical`. Manual trigger only. |
| 2 | Auto-trigger on PR events. Watch for runaway loops. Add `flywheel:pause` label. |
| 3 | Lower severity floor to `high`. Add CodeQL + Dependabot signals. Begin co-authored-by tracking. |
| 4 | Wire `pull_request_review_comment`. Validate that human comments trigger fixes. |
| 5 | Publish org workflow template. Onboard 5 more repos. |
| 6+ | Add `flywheel-nightly.yml` for stuck PRs > 24h. Stand up "fleet view" dashboard. |

---

## 13. Do / Don't

**Do:**
- Use a GitHub App token (so pushes retrigger the loop)
- Hard iteration cap (5) — non-negotiable
- Enable "dismiss stale approvals on push" scoped via Repository Ruleset path conditions (sensitive paths only)
- Strong tests + fast CI — autonomy depends on deterministic gates
- Co-authored-by trailer on every auto-commit
- Surface low-sev as comments only; never auto-fix them

**Don't:**
- Use default `GITHUB_TOKEN` — pushes won't retrigger Actions
- Let agent edit `.github/workflows/**` or secrets
- Force-push or amend — breaks reviewer mental model
- Auto-fix on every comment — bikeshedding loops
- Skip the `flywheel/merge-ready` status check
- Run on draft PRs (waste of API budget)

---

## 14. Ship Today vs. Add Later

**Day 1 (works immediately):**
- Reusable workflow + caller stub + Python orchestrator with Loop Engine
- Review Swarm with correctness + security reviewers (parallel dispatch)
- Stateful issue tracking via `loop-state.json` (cached across rounds)
- Claude Code Action or Copilot Coding Agent for fixes
- Copilot Code Review enabled via Ruleset for breadth
- Branch protection with `flywheel/merge-ready` + 1 human approval

**Add over the next few sprints:**
- Expand Review Swarm: add test coverage reviewer, style/maintainability reviewer
- Cron babysitter for stuck PRs
- Dual-model review ensemble (Claude + Copilot, dedupe findings)
- `flywheel-stats` dashboard reading workflow run artifacts (fix rate, avg iterations, escalation rate)
- Custom skills (`.github/agents/pr-review.agent.md` style) for repo-specific concerns
- Loop state persistence via workflow artifacts (in addition to cache)

---

## One-Sentence Takeaway

Stand up a reusable GitHub Actions workflow with a six-stage orchestrator lifecycle (Generate → Dispatch Review → Aggregate → Decide → Trigger Fix → Loop), a parallel Review Swarm (correctness + security reviewers producing structured findings), and a stateful Loop Engine tracking issues by ID across rounds — 5 iterations max, severity floor `high`, GitHub App token for write-back, and a `flywheel/merge-ready` status check that leaves the merge button to a human.
