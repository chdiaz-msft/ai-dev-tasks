# Existing-Tools Research: Local Issue Runner

**Purpose:** Determine whether open-source software already implements, or substantially overlaps, the proposed Local Issue Runner described in `tasks/local-issue-runner/spec.md`.
This report was produced from primary sources only: official repositories, source files, official documentation sites, and first-party GitHub API/CLI references.
Secondary sources (blog posts, aggregator articles) were used only for discovery of primary URLs and are flagged explicitly wherever they influenced a conclusion.
Research date: 2026-09-24.

## 1. What the Local Issue Runner needs to do

For reference, the Local Issue Runner is a Windows-first, single-developer, single-machine, single-repository Python/`uv` program that:

- Reads GitHub issue dependency and hierarchy relationships to compute implementation order across configured "parent" issues (each mapped to a feature branch) and "child" issues (each mapped to exactly one parent).
- Creates an isolated Git worktree and branch per eligible child issue and launches a coding agent, initially GitHub Copilot CLI, to implement it.
- Pushes the branch, opens a PR against the feature branch, waits for CI checks and an external auto-reviewer script to approve, and feeds failing tests or actionable review findings back to the coding agent for bounded repair attempts.
- Maintains durable local reconciliation state in a single SQLite database (job identity, ownership, attempt counts, review receipts pinned to a specific head-and-base commit pair, pauses, and recovery information), while treating GitHub and Git as the authoritative source of truth over any stored snapshot.
- Stages autonomy explicitly: `create_pr` only by default, then `merge_children` (auto-squash-merge child PRs and close child issues), then `merge_parents` (auto-merge an integration PR into main and close parent issues), each requiring an explicit configuration change.
- Runs one coding-agent or reviewer subprocess at a time, allows at most one runner-managed child PR per parent, and deliberately has no database server, message broker, distributed lock, web dashboard, or webhook listener.
- Uses authenticated `gh` CLI commands with structured JSON output for all GitHub access.

No project surveyed below implements this combination end to end.
The sections that follow document exactly where each project overlaps, where it does not, and how (if at all) it could be reused.

## 2. Summary table

| Project / area | Overlap with runner | License | Activity (verified) | Recommendation |
| --- | --- | --- | --- | --- |
| OpenHands (`OpenHands/OpenHands`) | Coding-agent execution; LLM-based iterative-refinement loop concept | MIT | Very active, pushed same day as research | Borrow refinement-loop pattern; avoid as infrastructure |
| SWE-agent (`SWE-agent/SWE-agent`) | Single-issue-to-patch agent; deterministic batch ordering pattern | MIT | Active but self-deprecated by maintainers in favor of `mini-swe-agent` | Borrow ordering pattern; avoid adopting directly |
| Aider (`Aider-AI/aider`) | Scriptable, non-interactive coding backend | Apache-2.0 | Stars high, but pushed 4+ months stale; public maintainer-availability questions on its own tracker | Integrate as optional backend; borrow CLI-invocation pattern |
| Sweep AI (`sweepai/sweep`) | Historical issue-to-PR bot concept; sandboxed check-and-repair loop concept | Proprietary "Enterprise Edition" license for advanced parts, MIT for the rest | Pivoted product; ~1 year stale on this use case | Avoid |
| GitHub Agentic Workflows (`github/gh-aw`) | Declarative agent-invocation spec; staged/validated-write concept | MIT | Very active, continuous releases | Borrow staged-write concept only; do not integrate |
| GitHub Copilot CLI | Directly documented non-interactive/programmatic coding-agent invocation | Proprietary (GitHub product; CLI usage governed by GitHub Copilot terms, not an OSS license) | Actively maintained, official GitHub product | Adopt as the coding-agent execution backend |
| GitHub Copilot cloud (coding) agent | Single-issue implement-and-branch automation with hosted sandboxing | Proprietary (GitHub product) | Actively maintained, official GitHub product | Not a substitute for the runner's orchestration; informative only |
| AutoPR (`irgolic/AutoPR`) | Single-issue plan-code-branch-PR loop | MIT | Formally archived 2026-03-05 | Avoid |
| GitHub sub-issues API | Native parent/child discovery | N/A (GitHub REST/GraphQL API, GA) | GA feature, actively documented | Adopt |
| GitHub issue dependencies API | Native blocked-by/blocking discovery | N/A (GitHub REST/GraphQL API, GA since 2025-08-21) | GA feature, actively documented | Adopt |
| `gh` CLI issue JSON fields | Structured read/write access to both relationship types | MIT (`cli/cli`) | Actively maintained; sub-issue/dependency flags added 2026 | Adopt |
| Temporal | Durable workflow/reconciliation concept | MIT | Very active | Avoid (requires a server) |
| Restate | Durable workflow/reconciliation concept | Business Source License 1.1 (not OSI-approved) | Active | Avoid (requires a server; restrictive license) |
| DBOS Transact (Python) | Durable workflow concept | MIT | Active | Avoid (requires PostgreSQL) |
| Procrastinate | Task-queue/durability concept | MIT | Active | Avoid (requires PostgreSQL) |
| Huey (`SqliteHuey`) | SQLite-backed task durability, no server | MIT-style permissive | Moderately active, last push ~9 days before research | Borrow pattern only; do not adopt |
| APScheduler | Interval scheduling with SQLite-backed job store | MIT | Active | Not needed; the runner's own poll loop already covers this |

**Bottom line: no surveyed project can replace the Local Issue Runner wholesale.**
Every issue-to-PR bot found is either a hosted/cloud-coupled service, a single-issue/single-shot tool with no dependency-ordered scheduling, or has been discontinued or de-prioritized by its own maintainers.
Every durable-workflow library that models idempotent, resumable execution as a first-class concept requires a server or a database server, which the spec explicitly rules out.
The two genuinely reusable, drop-in dependencies are GitHub's own sub-issues and issue-dependencies APIs (exposed natively through `gh` CLI JSON fields) and GitHub Copilot CLI's documented non-interactive invocation mode, both of which the spec already plans to use.

## 3. Coding-agent backends

### 3.1 OpenHands (`OpenHands/OpenHands`, formerly `All-Hands-AI/OpenHands`)

**What it is today.** The project has pivoted from a standalone "resolve GitHub issues" tool into "OpenHands Agent Canvas," a self-hosted developer control center for running OpenHands, Claude Code, Codex, Gemini, or any ACP-compatible agent across local, remote, or cloud backends (`OpenHands/OpenHands:README.md`).
The historical `openhands/resolver/` package and the separate `openhands-resolver` repository that used to implement a local GitHub issue resolver have been removed; that directory now returns HTTP 404 on the current default branch.

**What overlaps.** Issue and PR automation now lives in `docs.openhands.dev/openhands/usage/automations/event-automations`, built around GitHub event-based automations or scheduled automations that require installing a hosted OpenHands GitHub App and "claiming" the GitHub organization.
A "Critic (Experimental)" feature and an "Iterative Refinement" guide (`docs.openhands.dev/sdk/guides/critic.md`, `docs.openhands.dev/sdk/guides/iterative-refinement.md`) describe an LLM-judged repair loop conceptually similar to the runner's "feed failing checks back to the agent" step, but it is judged by an LLM critic rather than by real CI status and an external reviewer script pinned to a specific commit pair.
Persistence exists but is scoped to a single conversation (`docs.openhands.dev/sdk/guides/convo-persistence`, a `persistence_dir` with `base_state.json` and per-event JSON files), not a cross-issue reconciliation ledger.

**What does not overlap.** There is no git-worktree-per-issue model (isolation is via container or VM sandboxes), no dependency-ordered multi-issue scheduler, no SQLite-based job/attempt/review-receipt store, and no staged autonomy gating.
Adopting OpenHands as infrastructure would require installing a GitHub App and a hosted "team organization," which directly conflicts with the spec's single-machine, no-broker, no-dashboard requirement.

**Architectural fit.** Usable only as an alternative coding-agent backend behind the same interface already planned for Copilot CLI, not as a scheduler or orchestration layer.

**License and activity.** MIT (`OpenHands/OpenHands:LICENSE`).
Very active: `pushed_at` the same day as this research, 89,095 stars, 11,732 forks, 872 open issues, backed by an organization with a broader SDK ecosystem (`OpenHands/software-agent-sdk`).

**Recommendation.** Borrow the iterative-refinement/critic framing as a design reference for the repair loop; avoid adopting the project itself as infrastructure.

### 3.2 SWE-agent (`SWE-agent/SWE-agent`, formerly `princeton-nlp/SWE-agent`)

**Maintenance status.** The project's own README carries an explicit maintainer warning: most current development effort has moved to a sibling project, `mini-swe-agent`, and the maintainers' stated general recommendation is to use `mini-swe-agent` instead of SWE-agent going forward.

**What overlaps.** `sweagent run` resolves a single GitHub issue by URL, and `sweagent run-batch` supports deterministic, reproducible ordering over a fixed set of instances via `--instances.shuffle` and `--instances.slice` (`swe-agent.com/latest/usage/batch_mode/`).
That deterministic-ordering pattern is a reasonable design reference for the runner's own eligible-issue selection, even though `run-batch` operates over a static benchmark dataset rather than a live, dependency-ordered GitHub issue graph.

**What does not overlap.** SWE-agent produces a git diff or patch and a trajectory file as its output artifact; it does not push branches or open PRs as a first-class feature.
There is no CI-driven or external-reviewer-driven repair loop across separate PR pushes, and no durable, multi-job, ownership-tracked state beyond a single run's trajectory file.

**Architectural fit.** Viable only as an alternative or backup coding-agent backend, and the maintainers' own guidance points toward evaluating `mini-swe-agent` instead if a second backend is wanted.

**License and activity.** MIT (`SWE-agent/SWE-agent:LICENSE`).
Still receiving commits (`pushed_at` within days of this research, 20,398 stars), but explicitly de-prioritized by its own maintainers in favor of `mini-swe-agent` (7,952 stars, created 2025-06-28, actively growing).

**Recommendation.** Borrow the deterministic-ordering pattern; avoid adopting SWE-agent directly given the maintainers' own redirection.

### 3.3 Aider (`Aider-AI/aider`, formerly `paul-gauthier/aider`)

**What overlaps.** Aider has a documented non-interactive scripting mode (`aider.chat/docs/scripting.html`): `aider --message "instruction" file.py` with `--yes` to auto-confirm and `--auto-commits`/`--no-auto-commits` to control commit behavior, plus an explicitly unofficial and unsupported Python API (`Coder.create()`, `coder.run("instruction")` in `Aider-AI/aider:aider/coders/base_coder.py`).
This non-interactive contract is architecturally the same shape as how the runner already plans to invoke GitHub Copilot CLI: single instruction in, a git diff or commit out, then exit.

**What does not overlap.** Aider has no documented GitHub issue awareness, no built-in PR creation, no CI polling, and no reviewer-feedback loop.
Its scope stops at the local commit boundary.

**Architectural fit.** A plausible alternative or backup coding-agent backend behind the same interface as Copilot CLI; not a candidate for any scheduling or orchestration role.

**License and activity.** Apache-2.0 (`Aider-AI/aider:LICENSE.txt`).
Large user base (49,163 stars) but a materially slower cadence than the other two coding-agent projects: the GitHub API's `pushed_at` field showed the last push more than four months before this research, the latest release is v0.86.0, and the project's own issue tracker contains public discussion of maintainer availability (`Aider-AI/aider#4613`, `Aider-AI/aider#5647`).

**Recommendation.** Integrate as an optional secondary backend and borrow the non-interactive CLI-invocation pattern, but do not treat it as a primary dependency given the slowed maintenance cadence.

### 3.4 GitHub Copilot CLI (official)

**What overlaps.** GitHub's own documentation (`docs.github.com/en/copilot/concepts/agents/copilot-cli/about-copilot-cli`) confirms a "programmatic interface": `copilot -p "<prompt>" --allow-tool='shell(git)'` runs one prompt to completion and exits, which is exactly the subprocess contract the spec already plans to use.
Tool approval is granted per tool (`--allow-tool`) or globally (`--allow-all-tools`, with an explicit GitHub warning about the resulting scope of local access).
The docs also describe local sandboxing (`/sandbox enable`) and cloud sandboxing (`copilot --cloud`), both in public preview, as optional additional isolation layers on top of the runner's own git-worktree isolation.

**What does not overlap.** Copilot CLI is a coding-agent execution tool, not a scheduler; it has no concept of GitHub issue dependencies, PR lifecycles, or durable reconciliation state.
The spec already treats it exactly this way, as a subprocess invoked per child issue.

**License and activity.** Proprietary GitHub product, not an independently licensed open-source project; usage is governed by GitHub Copilot's terms rather than an OSS license.
Actively maintained as an official GitHub product.

**Recommendation.** Adopt, exactly as the spec already proposes, as the coding-agent execution backend.

### 3.5 GitHub Copilot cloud (coding) agent (official)

**What overlaps.** GitHub's hosted coding agent (`docs.github.com/en/copilot/concepts/agents/cloud-agent/about-cloud-agent`) can research a repository, create an implementation plan, and make code changes on a branch, triggered from GitHub Issues, VS Code, the Agents panel, `@copilot` PR-comment mentions, or a schedule.
It runs in its own ephemeral development environment powered by GitHub Actions and automates branch creation, commits, and pushes.

**What does not overlap.** The documentation shows no support for reading GitHub sub-issue or dependency relationships to compute cross-issue implementation order, no feature-branch and child-PR-and-parent-integration-PR hierarchy, no mechanism for a separate external local reviewer script's findings to drive an automated repair gate (it responds to human or `@copilot` PR comments, not an automated external verdict), and no durable local reconciliation state for interruption and restart safety.
Its isolation model is a hosted, GitHub-Actions-backed ephemeral environment rather than a local git worktree, which conflicts with the spec's requirement for local worktree isolation and a single local subprocess model.

**Architectural fit.** A plausible adjacent capability for "implement this one child issue," but it cannot substitute for the runner's scheduling, dependency-ordering, staged-autonomy, or review-feedback-loop responsibilities; those would still need to be built regardless of which coding-agent backend is chosen.
This is one reason the spec's choice of a locally invoked Copilot CLI subprocess, rather than delegating to the hosted cloud agent, is architecturally sound: it keeps execution inside the runner's own worktree and does not depend on GitHub Actions minutes or a hosted environment.

**Recommendation.** Treat as informative context only, not as a replacement for any part of the runner.

## 4. Issue-to-PR bots and agentic-workflow platforms

### 4.1 Sweep AI (`sweepai/sweep`)

**Current status.** Not formally archived on GitHub, but the product has pivoted away from the GitHub issue-to-PR bot entirely.
The repository's live README now states the team is building an AI coding assistant for JetBrains, and the repository's `description` and `homepage` fields have been updated accordingly.
The last push was roughly a year before this research, and `docs.sweep.dev` no longer resolves.

**What overlapped historically.** Sweep was a hosted GitHub App that watched issues by webhook and opened PRs, with an optional "Sandbox" CLI that ran configured lint, format, and test commands after every file edit as a check-and-repair gate (per the `sweep-sandbox-v1` release notes) — conceptually similar to, but narrower than, the runner's "wait for CI and an external reviewer, then repair" loop.

**What does not overlap.** No evidence of git-worktree-per-issue isolation, dependency-ordered multi-issue scheduling, or durable local reconciliation state; Sweep's state lived in its hosted service.

**License.** The repository's `LICENSE` file is a custom "Sweep Enterprise Edition" license: free for personal, non-commercial development and testing, but commercial or production use requires a paid subscription; only a portion of the code is MIT.
This is materially more restrictive than a standard open-source license.

**Recommendation.** Avoid.
The product line this project would overlap with has been discontinued, the architecture is a hosted GitHub App rather than a local poller, and the more advanced parts carry a non-permissive license.

### 4.2 GitHub Agentic Workflows (`github/gh-aw`, formerly under `githubnext`)

**What it is.** A `gh` CLI extension that compiles Markdown-plus-YAML-frontmatter "agentic workflow" specifications into standard GitHub Actions workflows, supporting GitHub Copilot, Claude Code, OpenAI Codex, Google Gemini, and Pi as underlying engines.
Per its architecture documentation, workflows run on GitHub-hosted or self-hosted Actions runners with container or microVM isolation, a network firewall, an API proxy, and an "MCP Gateway," with writes buffered through a "SafeOutputs" subsystem and applied by a separately scoped job.

**What overlaps.** The general shape of "declarative spec produces a bounded, scoped agent invocation whose writes are validated before being applied" is philosophically similar to the runner's staged autonomy gating (`create_pr` → `merge_children` → `merge_parents`).

**What does not overlap.** `gh-aw` fundamentally executes inside GitHub Actions, not on a local machine; its state is Actions run history and artifacts, not a local SQLite reconciliation ledger; and it has no concept of a child-issue-to-branch-to-child-PR-to-feature-branch-to-parent-integration-PR hierarchy or dependency-ordered scheduling.
Adopting it would mean moving orchestration into GitHub Actions, which conflicts directly with the spec's single-machine, no-broker requirement.

**License and activity.** MIT (`github/gh-aw:LICENSE`).
Very active: created 2025-08-12, with releases cut essentially continuously and both `pushed_at` and `updated_at` on the day of this research.

**Recommendation.** Borrow the "SafeOutputs" staged-and-validated-write concept as a design reference for the autonomy levels; do not integrate the project itself.

### 4.3 AutoPR (`irgolic/AutoPR`)

**Status.** Formally archived, with the final commit's message reading "README: update for archival" (2026-03-05), confirming deliberate discontinuation rather than mere staleness.

**What it did.** Given an issue, it planned a fix, wrote code, pushed a branch, and opened a PR, distributed as a GitHub Action and later a CLI/Python library, with a `workon <issue_number>` / `commit` / `pr` command shape.

**What does not overlap.** No multi-issue dependency ordering, no worktree-per-issue isolation as a first-class model, no CI-or-external-reviewer feedback loop, and no durable reconciliation state; it was single-issue, single-shot, alpha-quality software by its own documentation's caveats.

**License and activity.** MIT.
Archived; zero open issues remaining at archival time.

**Recommendation.** Avoid.
No other actively maintained open-source issue-to-PR bot with dependency-ordered, multi-issue scheduling was found during this research; this appears to be a genuine capability gap that the Local Issue Runner would fill, though this is not a claim of exhaustive search coverage.

## 5. GitHub issue dependency and sub-issue APIs

GitHub now provides two separate, official, generally-available relationship models, confirmed by direct fetch of the current documentation:

**Sub-issues (hierarchy).**
Documented at `docs.github.com/en/rest/issues/sub-issues`, with REST endpoints including `GET /repos/{owner}/{repo}/issues/{issue_number}/parent`, `GET /repos/{owner}/{repo}/issues/{issue_number}/sub_issues`, and `DELETE /repos/{owner}/{repo}/issues/{issue_number}/sub_issue`, plus add and reprioritize endpoints documented on the same page.
Ordinary issue GET responses now embed a `sub_issues_summary` object (`total`, `completed`, `percent_completed`) and a `parent_issue_url` field directly on the Issue resource.
No public-preview banner is present on this page; it renders as a standard, versioned, generally-available REST resource.

**Issue dependencies (blocking).**
Documented at `docs.github.com/en/rest/issues/issue-dependencies`, a distinct resource from sub-issues, with endpoints such as `GET /repos/{owner}/{repo}/issues/{issue_number}/dependencies/blocked_by` and corresponding `blocking` and mutation endpoints on the same page.
The Issue resource also carries an `issue_dependencies_summary` field (`blocked_by`, `blocking`, `total_blocked_by`, `total_blocking`).
GitHub's own changelog confirms this feature became generally available on 2025-08-21 (`github.blog/changelog/2025-08-21-dependencies-on-issues/`), stating dependencies are fully supported in the API and webhooks, with UI search qualifiers `is:blocked`, `is:blocking`, `blocked-by:`, and `blocking:`, and a limit of 50 linked issues per relationship type.

This means blocking and sub-issue hierarchy are two distinct, native, first-class GitHub features, not a task-list-text convention.
For the Local Issue Runner's design, parent-and-child structure should be read through the sub-issues API or its `gh` CLI equivalent, while ordering constraints such as "must be implemented before" between sibling children should be read through the issue-dependencies API or its `gh` CLI equivalent, rather than being inferred purely from sub-issue nesting.

**`gh` CLI support.**
Direct fetch of `cli.github.com/manual/gh_issue_view` confirms `gh issue view --json` supports both relationship families as first-class fields, including `parent`, `subIssues`, `subIssuesSummary`, `blockedBy`, and `blocking`, alongside standard fields such as `state`, `labels`, and `milestone`.
Write access is documented at `docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues`, for example `gh issue create --parent PARENT-ISSUE-NUMBER`, `gh issue edit PARENT-ISSUE-NUMBER --add-sub-issue SUB-ISSUE-NUMBER`, and `gh issue edit PARENT-ISSUE-NUMBER --remove-sub-issue SUB-ISSUE-NUMBER`.
GitHub's own changelog additionally confirms that GitHub CLI v2.94.0 and later added `--parent`, `--set-parent`, `--remove-parent`, `--blocked-by`, and `--blocking` flags with matching JSON fields (`github.blog/changelog/2026-06-10-manage-sub-issues-types-and-dependencies-from-github-cli/`).

**Caveats.**
GraphQL-level field detail (`Issue.subIssues`, `Issue.parent`, the `addSubIssue` mutation, and whether a `GraphQL-Features: sub_issues` preview header is still required) could not be confirmed directly in this session because the GraphQL reference page renders client-side; if the runner ever needs GraphQL rather than REST or `gh`, this should be verified with an authenticated introspection query before depending on it.
Legacy markdown task-list tracking (`trackedIssues`/`trackedInIssues`) is a separate, older, read-only mechanism that predates both native features and was not independently confirmed against a primary doc in this session; it should not be relied upon given the two GA features above.

**Recommendation.** Adopt.
`gh issue view --json parent,subIssues,subIssuesSummary,blockedBy,blocking` fully satisfies the spec's stated design of using authenticated `gh` commands with structured JSON output, matching the spec's own stated preference for native sub-issue discovery with a fallback to an explicit configured list of child issues.

## 6. Durable workflow and reconciliation-state libraries

The spec deliberately wants no database server, message broker, or distributed lock; state must live in a single local SQLite database, with GitHub and Git as the authoritative source of truth.
The following general-purpose durable-execution and task-queue libraries were evaluated against that constraint.

**Temporal (`temporalio/temporal`).**
Requires a standalone Temporal Service (server) process even for local development; `temporal server start-dev` starts a full local server with its own persistence, task queues, and matching-and-history services.
This is not an embeddable library, so it fails the "no server" constraint outright.
MIT-licensed, very active (pushed the same day as this research, 23,278 stars).
Recommendation: avoid.

**Restate (`restatedev/restate`).**
Ships as a standalone `restate-server` binary or container exposing multiple ports, which the app talks to over HTTP; this is the same "mandatory server" disqualification as Temporal.
Licensed under the Business Source License 1.1, which is not an OSI-approved open-source license (it converts to Apache-2.0 after four years).
Active (pushed the same day as this research, 4,470 stars).
Recommendation: avoid, both for architectural fit and license restrictiveness.

**DBOS Transact (Python) (`dbos-inc/dbos-transact-py`).**
Requires PostgreSQL as its durability substrate; there is no SQLite backend.
Postgres itself is a database server process, which the spec's "no database server" language rules out even though DBOS itself needs no additional broker.
MIT-licensed, active.
Recommendation: avoid.

**Procrastinate (`procrastinate-org/procrastinate`).**
Requires PostgreSQL 13 or later for both its queue and its lock manager, the same disqualification as DBOS.
MIT-licensed, active.
Recommendation: avoid.

**Huey (`coleifer/huey`, `SqliteHuey`).**
The closest architectural match among the surveyed libraries: it has no required server or broker, and `SqliteHuey` is a genuine, first-class, zero-dependency embedded backend.
However, Huey is built around a consumer/worker process model with its own enqueue/dequeue, retry, priority, and pipeline semantics, a superset of general task-queue concerns that a single-process poll-and-reconcile design does not need.
Its data model is queue-shaped rather than reconciliation-shaped; it has no concept of idempotent job identity keyed on a specific head-and-base commit pair, or of review receipts pinned to a commit pair.
MIT-style license, moderately active (last push roughly nine days before this research).
Recommendation: borrow the SQLite storage and locking approach as a reference only; adopting the library wholesale would import queue and worker abstractions the runner's design does not use.

**APScheduler (`agronholm/apscheduler`).**
A pure-Python interval-and-cron scheduler with pluggable job stores, including a documented SQLite-backed `SQLAlchemyJobStore`.
No server or broker required.
However, it solves "run something on an interval," which the spec's own five-minute poll loop already trivially handles, and it contributes nothing to the harder problem of durable job, attempt, and review-receipt tracking with idempotency guarantees.
MIT-licensed, active.
Recommendation: not needed; the runner's own poll loop already covers this.

**Conclusion for this area.**
No surveyed general-purpose durable-workflow or task-queue library is a good fit as a dependency.
Every library that models idempotent, resumable execution as a first-class concept (Temporal, Restate, DBOS) does so by requiring an external server or database daemon, which the design brief explicitly rules out.
The two libraries compatible with a "no server" constraint (Huey, APScheduler) solve adjacent problems, generic task queueing and generic interval scheduling, rather than the runner's actual need: a small number of purpose-built, domain-specific state-machine tables recording job identity, attempt counts, review receipts scoped to a head-and-base commit pair, and pause-and-resume flags, reconciled against GitHub and Git as authoritative facts on every poll tick.
A bespoke SQLite schema, exactly as the spec already proposes, is the more appropriate and simpler pattern: it is smaller, more auditable, and expresses the "receipt tied to a specific head-and-base commit pair, GitHub always wins" reconciliation model directly, rather than adapting a queue or scheduler abstraction that was not designed to express it.

## 7. Overall answer to "does something already do this?"

No open-source project or officially documented GitHub feature implements the Local Issue Runner's specific combination of capabilities: dependency-ordered parent-and-child issue scheduling, git-worktree-per-child isolation, a coding-agent repair loop driven by real CI status and an external reviewer's verdict pinned to a specific commit pair, durable local SQLite reconciliation surviving interruption and restart, and staged autonomy from PR creation only through child-merge to parent-merge.

Every issue-to-PR bot surveyed (Sweep, AutoPR) is either a hosted, cloud-coupled service or an archived, single-issue, single-shot tool, with no dependency-ordering or worktree-isolation model.
Every general-purpose coding-agent framework surveyed (OpenHands, SWE-agent, Aider) is a viable execution backend for "implement this one issue" but has no scheduling, PR-lifecycle, or durable-state layer of its own, and each has either pivoted toward a hosted/cloud architecture (OpenHands), been de-prioritized by its own maintainers (SWE-agent), or slowed materially in maintenance cadence (Aider).
GitHub's own official tooling, GitHub Agentic Workflows and the Copilot cloud agent, both solve adjacent problems inside GitHub Actions or a hosted environment rather than on a local machine, and neither has dependency-ordered multi-issue scheduling.
Every durable-workflow library surveyed either requires a standalone server process or a database server, which the spec's architecture explicitly rules out.

The two components that are directly reusable, and that the spec already plans to use, are GitHub's native sub-issues and issue-dependencies REST APIs (fully exposed through `gh` CLI JSON fields, requiring no custom API client) and GitHub Copilot CLI's documented non-interactive `-p`/`--allow-tool` invocation mode (a sanctioned, stable integration point for the coding-agent subprocess step).
Building the scheduler, worktree manager, PR-lifecycle state machine, and SQLite reconciliation ledger as originally scoped remains the correct path; no existing project can replace that layer wholesale.

## 8. Sources consulted

- `OpenHands/OpenHands` — README, LICENSE, and `docs.openhands.dev` (automations, workspace, sandbox, critic, iterative-refinement, and conversation-persistence guides).
- `OpenHands/software-agent-sdk` — example GitHub Actions workflows referenced from `docs.openhands.dev`.
- `SWE-agent/SWE-agent` — README, LICENSE, and `swe-agent.com` (hello-world and batch-mode usage docs).
- `SWE-agent/mini-swe-agent` — GitHub API repository metadata.
- `Aider-AI/aider` — LICENSE.txt, `aider.chat/docs/scripting.html`, `aider.chat/docs/git.html`, `aider/coders/base_coder.py`, GitHub Releases page, and issues `#4613` and `#5647`.
- `sweepai/sweep` — README, LICENSE, GitHub API repository metadata, and the `sweep-sandbox-v1` release notes.
- `github/gh-aw` (and its `githubnext/gh-aw` redirect) — README, LICENSE, and `github.github.com/gh-aw` architecture and CLI-setup documentation.
- `docs.github.com/en/copilot/concepts/agents/copilot-cli/about-copilot-cli` — Copilot CLI programmatic interface, tool-approval flags, and sandboxing documentation.
- `docs.github.com/en/copilot/concepts/agents/cloud-agent/about-cloud-agent` — Copilot cloud agent documentation.
- `irgolic/AutoPR` — GitHub API repository metadata and final commit.
- `docs.github.com/en/rest/issues/sub-issues` and `docs.github.com/en/rest/issues/issue-dependencies` — REST API reference.
- `github.blog/changelog/2025-08-21-dependencies-on-issues/` and `github.blog/changelog/2026-06-10-manage-sub-issues-types-and-dependencies-from-github-cli/` — official GitHub changelog entries.
- `cli.github.com/manual/gh_issue_view` and `docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues` — `gh` CLI reference.
- `temporalio/temporal` — LICENSE and GitHub API repository metadata.
- `restatedev/restate` — README, LICENSE, and GitHub API repository metadata.
- `dbos-inc/dbos-transact-py` — README, LICENSE, and GitHub API repository metadata.
- `procrastinate-org/procrastinate` — README and GitHub API repository metadata.
- `coleifer/huey` — README.rst, LICENSE, and GitHub API repository metadata.
- `agronholm/apscheduler` — LICENSE.txt, `apscheduler.readthedocs.io` job-store documentation, and GitHub API repository metadata.
