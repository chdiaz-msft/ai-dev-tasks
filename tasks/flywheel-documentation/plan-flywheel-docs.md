# Plan: PR Flywheel Documentation

## Problem

The PR flywheel system (orchestrator/, workflows, prompts) lacks proper documentation.
The README was thin (120 lines) and there was no dedicated orchestrator internals doc.
This plan covers documenting the full system as implemented on the `feature/enhanced-design-flow` branch.

## Current State

- **README.md** — already rewritten from 120 → 282 lines with:
  - Architecture data-flow diagram (ASCII)
  - Decision engine state machine (5 states + transition priority)
  - Issue lifecycle (statuses + 3 automatic resolution methods)
  - Severity scale table
  - Safety rails (10 documented safeguards)
  - Quick start with full adoption checklist (secrets, app permissions, branch protection)
  - Workflow descriptions (flywheel.yml + nightly babysitter)
  - Module reference table for all 6 orchestrator modules
  - Troubleshooting table
  - Prompts & reviewers section with extension instructions

- **orchestrator/README.md** — does not exist yet

## Approach

Two-file documentation structure:
- `README.md` → adoption/operator guide (what it does, how to install, how to use)
- `orchestrator/README.md` → technical internals reference (data structures, algorithms, extension points)

All changes committed as a single commit.

## Todos

### 1. create-orchestrator-readme
**Create `orchestrator/README.md`**

Write a technical internals doc covering:
- Package overview and module map
- Data structures with field tables:
  - `Issue` (11 fields)
  - `LoopState` (6 fields)
  - `TerminationStatus` enum
  - `IssueStatus` enum
- Decision engine logic: the full `decide()` priority chain
- Issue lifecycle: open → resolved (3 automatic methods), wont_fix, escalated
- Severity classifier: SEV_ORDER, filter_by_floor, classify_severity
- Signal aggregator: all signal sources, GraphQL queries, output schema
- Review swarm: parallel dispatch, timeout handling, graceful degradation
- Fix dispatcher: agent selection logic, payload cap (10), forbidden path patterns
- Extension points (adding a new reviewer: prompt + registration)
- Test suite overview and commands

### 2. trim-readme-internals
**Move deep internals from README.md to orchestrator/README.md**

Per direction, relocate detailed module-level content out of the main README
into orchestrator/README.md. In README.md, leave concise summaries with
`[See orchestrator/README.md](orchestrator/README.md)` links. Specifically move:
- Decision priority chain details (keep the state table, move the numbered list)
- Issue lifecycle automatic resolution method details
- Severity numeric mapping table

### 3. verify-links
**Verify all cross-links between README.md and orchestrator/README.md**

Ensure relative links work and no broken references exist.

### 4. commit-docs
**Commit all documentation changes**

Single commit with message:
```
docs: comprehensive PR flywheel documentation

- Rewrite README.md as adoption/operator guide with architecture
  diagrams, state machine, safety rails, and troubleshooting
- Create orchestrator/README.md as technical internals reference
  with data structures, decision engine, and extension points

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

## Design Decisions

| Decision | Rationale |
| --- | --- |
| Two-doc split | Keeps README scannable for adopters; internals doc serves contributors |
| ASCII diagrams over Mermaid | Renders everywhere (terminals, GitHub, editors) without JS |
| Separate decision states from termination statuses | They map differently (e.g., `ready` → `success`) |
| Document `wont_fix`/`escalated` as model-level only | Controller doesn't automate these transitions |
| Include troubleshooting table | Most common failure modes are non-obvious to new adopters |

## Files Changed

| File | Action |
| --- | --- |
| `README.md` | Rewrite (done) + trim internals |
| `orchestrator/README.md` | Create (new file) |

## Files NOT Changed

- `prompts/*` — already well-documented
- `orchestrator/*.py` — source code unchanged
- `orchestrator/tests/*` — tests unchanged
- `.github/workflows/*` — workflows unchanged
