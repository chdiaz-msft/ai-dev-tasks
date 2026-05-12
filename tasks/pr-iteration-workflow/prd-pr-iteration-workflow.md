# PRD: PR Iteration Workflow

## Introduction / Overview

Manual code reviews are slow and create bottlenecks in the development cycle. This feature introduces an automated **review → triage → fix** loop for pull requests. A shell script orchestrates multiple AI review agents that inspect a PR's diff, post blocker-level findings as inline comments, then triage and auto-fix those findings — repeating until the PR is clean or a maximum iteration count is reached.

The workflow reuses the existing 5-track Python code review framework (`python_code_review_agents.md` / `python_code_review_guidelines.md`) and follows the established `task-helpers/ralph-wiggum-v2.sh` conventions for headless Claude orchestration.

## Goals

1. **Automate end-to-end PR review cycles** — from initial review through triage and fixing, with no human intervention required.
2. **Focus on blockers only** — filter out low-severity suggestions so the loop converges quickly on issues that matter.
3. **Work on any PR** — whether authored by a human or an AI agent (e.g., ralph-wiggum output).
4. **Iterate to convergence** — repeat the loop until zero blockers remain or a hard cap of 3 iterations is hit.
5. **Signal review completion** — when the loop exits, post a summary comment indicating the review outcome (clean or blockers remaining) so the user knows the review is done.

## User Stories

- **As a developer**, I want to run a single command against my PR and have it automatically reviewed, triaged, and fixed so that I don't have to wait for a human reviewer.
- **As a team lead**, I want PR reviews to be consistent and thorough (using the 5 review tracks) so that code quality is maintained regardless of who authored the PR.
- **As an AI agent operator**, I want ralph-wiggum's output PRs to be automatically polished by a review loop so that they meet quality standards before a human ever looks at them.
- **As a developer**, I want to see what the review agents found (as inline PR comments) so that I can learn from the feedback even though fixes are automated.

## Functional Requirements

### Script Interface

1. The script shall be named `pr-iteration.sh` and placed at the repository root.
2. The script shall accept the following arguments:
   - **Positional:** `<PR_NUMBER>` — the GitHub pull request number to review.
   - `--model <model>` — Claude model to use (default: `sonnet`).
   - `--permission-mode <mode>` — permission mode for Claude's **fix agents** in Phase 3 (default: `edit`). Review and triage phases (Phases 0–2) always run in `plan` mode (least-privilege, read-only). Only the fix phase requires a mode capable of editing files. Elevated modes like `bypassPermissions` should only be used when explicitly opted in.
   - `--print-only` — dry-run mode that prints prompts without executing.
   - `--verbose` / `-v` — enable debug logging.
   - `--resume <run_id>` — resume a previous run from the last completed phase, using state persisted in `.pr-iteration-logs/<run_id>/state.json`.
   - `--force` — allow running on PRs with no Python files (review tracks are Python-specific by default).
3. The maximum number of review iterations shall be hardcoded to **3**.
4. The script shall follow `task-helpers/ralph-wiggum-v2.sh` conventions: color-coded logging (`log`, `ok`, `warn`, `err`, `header`), structured output, and `set -euo pipefail`.

### Prerequisites & Validation

5. The script shall verify that `gh` CLI is authenticated and available.
6. The script shall verify that `claude` CLI is available.
7. The script shall verify that the PR branch is checked out locally (compare current branch with `gh pr view --json headRefName`).
8. The script shall fetch the PR diff via `gh pr diff <PR_NUMBER>`.
9. If the PR diff contains no Python files, the script shall warn that the review tracks are Python-specific and exit with a non-zero status, unless the `--force` flag is provided.

### Phase 0 — PR Validation

9a. **External Fork Detection** — The script shall check whether the PR originates from an external fork by comparing `headRepository.owner.login` with the base repository owner via `gh pr view --json headRepository,baseRepository,isCrossRepository`. If the PR is cross-repository (external fork), the script shall run in **review-only mode**: Phases 0–2 execute normally, but Phase 3 (fixing) is skipped entirely. The script shall post a comment explaining that automated fixes are disabled for external fork PRs and exit with a zero status after completing the review. The `--force` flag shall not override this restriction.
10. **Size & Complexity Check** — The script shall compute deterministic size metrics from the PR diff: files changed, lines added/removed, and number of hunks. If the PR exceeds configurable thresholds (default: >500 lines changed or >15 files), the script shall emit a warning that the PR may be too large for effective automated review and recommend splitting it. The script shall **request a smaller PR** (post a comment and exit with a non-zero status) when any of the following deterministic conditions are met:
    - The PR body is empty or there is no linked implementation plan.
    - CI is failing and the only changes in the diff are to test files.

    Additionally, the script shall invoke a `claude -p` call to assess whether the PR covers multiple unrelated objectives. If the agent determines the PR is unfocused, it shall post an advisory warning as a PR comment (but shall **not** block the run).
11. **CI Configuration Scrutiny** — The script shall scan the diff for changes to CI/CD configuration files (e.g., `.github/workflows/*.yml`, `Makefile`, `Dockerfile`, `.pre-commit-config.yaml`, `tox.ini`, `pyproject.toml` build/CI sections). Any such changes shall be flagged with a prominent warning indicating they require careful human scrutiny, and the specific files and change summaries shall be listed. This check is deterministic (grep/pattern-matching on diff file paths).
12. **New Function Duplicate Detection** — Rather than building a standalone parser in the shell script, this check shall be folded into the review agents' prompt context. Each Phase 1 review agent shall receive an instruction to check whether any newly defined functions/methods in the diff duplicate existing repository functions by name or signature. Any potential duplicates shall be reported as Blocker findings with the existing function's file path so reviewers can determine if the new code is redundant.

### Phase 1 — Review

13. The script shall dispatch **5 parallel `claude -p` calls**, one per review track from `python_code_review_agents.md`:
   - Track 1: Correctness, Contracts & Safety (§A, §C, §D, §I)
   - Track 2: Type System & Modeling (§B)
   - Track 3: Architecture & Design (§E, §F)
   - Track 4: Tests (§G)
   - Track 5: Production Readiness (§H, §J)
14. Each review agent shall receive:
    - The full PR diff.
    - The contents of `python_code_review_guidelines.md`.
    - Its track assignment (focus area + assigned sections).
    - An instruction to check whether any newly defined functions/methods in the diff duplicate existing repository functions by name or signature.
    - Instructions to output structured findings as **JSON Lines** (one JSON object per line): `{"severity":"blocker|suggestion","file":"<path>","line":<n>,"start_line":<n|null>,"side":"RIGHT|LEFT","explanation":"<text>"}`. The `line` field is the end line of the finding in the diff. `start_line` is optional and used for multi-line findings. `side` defaults to `"RIGHT"` (additions); use `"LEFT"` for findings on deleted lines.
15. Review agents shall classify findings as either **Blocker** or **Suggestion**.
16. After all 5 agents complete, the script shall aggregate findings, deduplicate (keep the more specific finding when two tracks flag the same line), and **discard all Suggestion-level findings**.
16a. **Finding coordinate validation** — Before posting, the script shall validate each finding's `file` and `line` against the PR diff to confirm they correspond to a valid diff hunk position. Findings that cannot be mapped to a valid diff line (e.g., file-level issues, deleted files, or lines outside any hunk) shall be posted as part of the review body text (not inline), with the file path and line number referenced in the comment text.
17. Before posting new findings, the script shall fetch all unresolved comments matching the `<!-- pr-iteration:` marker prefix (from any run, not just the current `run_id`) and skip any new finding whose **fingerprint** matches an existing unresolved comment. The fingerprint is a deterministic hash of `(file, line, normalized_explanation)` and is embedded in each comment marker as `<!-- pr-iteration:run:<run_id>:fp:<fingerprint> -->`. This prevents duplicate comments across re-runs on the same PR.
18. The script shall post remaining Blocker findings as **inline review comments** on the PR via `gh api`, targeting the specific file and line.
19. If zero Blocker findings are posted, the review phase shall signal "clean" and skip triage/address phases.
20. **Critical Path Tracing** — After the 5-track review completes, the script shall invoke an additional `claude -p` call that identifies the most important logic change in the diff and traces it end-to-end: input → transforms → output. This agent shall specifically check boundary conditions, permission checks, and unexpected branching paths. Findings from this trace shall be output as JSON Lines and merged into the aggregated results. This step is mandatory and must not be skipped.
21. **Regression Test Requirement** — For any non-trivial logic change identified in the diff, the review shall verify that the PR includes a test which would fail against the pre-change behavior. If such a test is missing, a Blocker finding shall be raised requiring the author to add one.
22. **Rollback Plan for Risky Changes** — For changes flagged as risky (e.g., data migrations, schema changes, feature flag removals, security-sensitive modifications), the review shall check whether the PR description or comments include a rollback plan. If no rollback plan is present, a Blocker finding shall be raised requesting one.

### Phase 2 — Comment Triage

23. The script shall fetch all **unresolved review comments owned by this script** (identified by the `<!-- pr-iteration:` marker prefix) on the PR via `gh api graphql`. Human-authored comments and comments from other tools shall be excluded from triage.
24. The script shall invoke a single `claude -p` call with all unresolved comments, instructing Claude to evaluate each comment and output a decision as **JSON Lines**: `{"comment_id":"<id>","action":"FIX","plan":"<brief_plan>"}` or `{"comment_id":"<id>","action":"IGNORE","justification":"<text>"}`.
25. For each comment triaged as **IGNORE**, the script shall resolve the comment thread via `gh api`, posting the justification as a reply before resolving.
26. If zero comments are triaged as **FIX**, the triage phase shall signal "clean" and exit the loop.

### Phase 3 — Comment Addressing

27. Fix agents shall run **serially** (one at a time) to prevent concurrent modifications to the working tree. For each comment triaged as **FIX**, the script shall invoke a `claude -p` call that:
    - Receives the comment text, file path, line number, and the triage plan.
    - Applies the fix to the local codebase.
    - Outputs a summary of what was changed.
27a. **Clean working tree prerequisite** — Before entering Phase 3, the script shall verify the working tree is clean (`git status --porcelain` returns empty). If the tree is dirty, the script shall abort with an error message instructing the user to commit or stash changes first.
27b. **Post-fix scope validation** — After each fix agent completes, the script shall run `git diff --name-only` and verify that only files present in the original PR diff were modified. If any out-of-scope file was modified, the script shall discard those changes (`git checkout -- <file>` for each out-of-scope file), log a warning identifying the discarded files, and continue with the remaining fixes.
28. After all fixes are applied, the script shall run the repository's lint, test, and build commands (if configured via a `pr-iteration.conf` or detected from `pyproject.toml` / `Makefile`). If any validation step fails, the script shall abort the push, log the failure details, and exit with non-zero status.
29. If validation passes, the script shall:
    - Stage and commit all changes with a message: `fix: address review comments (iteration N)`.
    - Push to the PR branch.
    - Resolve each addressed comment thread via `gh api`, posting a reply describing the fix.

### Iteration Loop

30. The script shall repeat Phase 0 → Phase 1 → Phase 2 → Phase 3 up to **3 times**. Phase 0 (PR Validation) runs only on the first iteration.
31. The loop shall exit early if:
    - Phase 1 finds zero Blocker findings, OR
    - Phase 2 triages all remaining comments as IGNORE (these are considered resolved since they receive a reply and are resolved in req 25). A "clean exit" means zero unresolved comment threads owned by this run, not zero blocker findings ever posted.
32. When the loop exits cleanly (no remaining unresolved blockers), the script shall **post a summary comment** on the PR indicating the review is complete with no outstanding blockers.
33. When the loop exits after hitting the 3-iteration cap with blockers still present, the script shall post a summary comment listing unresolved issues and exit with a non-zero status.

### Logging & Output

34. The script shall create a log directory (`.pr-iteration-logs/<run_id>/`) containing:
    - Per-track review agent output files.
    - **Phase 0 validation report** (size metrics, CI change flags).
    - **Critical path trace output.**
    - Triage decision output files.
    - Fix agent output files.
    - A summary log with iteration counts and outcomes.
    - `state.json` — iteration state file recording current iteration number, phase, and posted comment IDs (used for `--resume`).
35. The script shall print a colored summary after each iteration showing: iteration number, findings count, FIX/IGNORE counts, and files modified.

## Non-Goals (Out of Scope)

- **CI/CD integration** — this is a local CLI tool, not a GitHub Action (for now).
- **Non-Python review guidelines** — the review tracks are hardcoded to the Python code review framework. Generalizing to other languages is a future enhancement.
- **Partial/incremental review** — each iteration reviews the full diff, not just changes since the last iteration.
- **Interactive mode** — the script runs fully autonomously with no user prompts during execution.
- **Branch management** — the script does not create branches or manage merges; the PR branch must already be checked out.

## Technical Considerations

- **Dependencies:** Requires `gh` CLI (authenticated), `claude` CLI, `git`, and `bash`.
- **Parallel execution:** Review agents run as background processes (`claude -p ... &`) with `wait` to synchronize. Output is captured to temp files to avoid interleaving.
- **GitHub API for inline comments:** Posting inline review comments requires the `POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews` endpoint with `comments` array specifying `path`, `line`, and `body`. The current PR head SHA (`gh pr view --json headRefName,headRefOid`) must be used as the `commit_id` when creating reviews. When a finding cannot be mapped to a valid diff line (e.g., file-level issue), it shall be posted as a top-level review comment instead.
- **Resolving comment threads:** Use the GraphQL `resolveReviewThread` mutation via `gh api graphql`. This requires fetching review thread IDs (not just comment IDs) via `pullRequest.reviewThreads`. The REST `minimizeComment` endpoint is **not** equivalent and shall not be used for thread resolution.
- **Comment ownership:** The script shall tag all posted comments with a marker containing both the run ID and a stable finding fingerprint: `<!-- pr-iteration:run:<run_id>:fp:<fingerprint> -->`. The fingerprint is a deterministic hash of `(file, line, normalized_explanation)`. Only comments bearing the `<!-- pr-iteration:` prefix shall be triaged, resolved, or addressed. Human-authored comments shall never be auto-resolved. Fingerprint-based deduplication operates across all runs on the same PR (see req 17).
- **Rate limiting:** 5 parallel Claude calls per iteration × 3 iterations = up to 15+ Claude calls. Plan run timing accordingly.
- **Existing conventions:** Follow `task-helpers/ralph-wiggum-v2.sh` patterns for arg parsing, color output, Claude invocation, status parsing, and log management.
- **Security safeguards:** PR diffs and review comments are untrusted input. The script shall: (1) use `plan` mode for review/triage phases and the user-specified `--permission-mode` (default `edit`) only for fix agents, (2) enforce file-scope constraints on fix agents via post-fix `git diff --name-only` validation (see req 27b) — prompt-only constraints are insufficient, (3) never include secrets or tokens in prompts or logs, (4) skip Phase 3 entirely for PRs from external forks (see req 9a), and (5) require a clean working tree before Phase 3 (see req 27a).
- **Resume support:** On startup with `--resume <run_id>`, the script shall read `.pr-iteration-logs/<run_id>/state.json` and resume from the last completed phase. If `state.json` is missing or corrupt, the script shall warn and start fresh.
- **Non-Python PRs:** The review tracks are Python-specific. The script exits with a warning on non-Python PRs unless `--force` is provided. Future work may add language-agnostic or multi-language review tracks.

## Success Metrics

- The script can review a real PR end-to-end and produce meaningful inline comments.
- The triage phase correctly distinguishes actionable blockers from noise.
- The fix phase successfully modifies code to address review comments.
- The loop converges (exits before hitting the 3-iteration cap) on typical PRs.
- Clean PRs are flagged as review-complete via a summary comment so the author knows no further action is needed.

## Design Decisions — Tooling & Delegation

Analysis by Claude Opus 4.7 and GPT-5.4 identified that ~80% of requirements map directly to existing tools. The following decisions capture the delegation strategy and deferred work.

### Delegation Strategy

Each requirement is classified as one of:
- **DELEGATE** — existing tool handles completely, zero custom code
- **CONFIGURE** — existing tool handles with prompting/configuration only
- **THIN WRAPPER** — small glue code (<30 lines) around existing tools
- **BUILD** — no existing tool; must write custom

#### DELEGATE (zero custom code)

| Requirement | Tool/Command |
|---|---|
| Verify `gh` auth (req 5) | `gh auth status` |
| Verify `claude` CLI (req 6) | `command -v claude` |
| Verify/checkout PR branch (req 7) | `gh pr checkout <N>` or `gh pr view --json headRefName` |
| Fetch PR diff (req 8) | `gh pr diff <N>` |
| External fork detection (req 9a) | `gh pr view <N> --json isCrossRepository` |
| Clean working tree check (req 27a) | `git status --porcelain` |
| Lint/test/build validation (req 28) | `uv run ruff check .` / `uv run pyright` / `uv run pytest` |
| Stage/commit/push (req 29) | `git add -A && git commit && git push` |
| Resolve threads (req 25, 29) | `gh api graphql` → `resolveReviewThread` mutation |
| Post summary comments (req 32-33) | `gh pr comment <N> --body "..."` |
| Post inline review comments (req 18) | `gh api repos/{o}/{r}/pulls/{N}/reviews -X POST` |
| Color/logging helpers (req 35) | Reuse `ralph-wiggum-v2.sh` helpers |
| Loop/control flow (req 19, 26, 30-31) | Bash conditionals and loops |

#### CONFIGURE (prompt engineering only)

| Requirement | Approach |
|---|---|
| 5-track parallel review (req 13-15) | `claude -p` × 5 background jobs; prompts from existing `python_code_review_agents.md` |
| Duplicate function detection (req 12) | Add instruction to review agent prompts |
| Critical path tracing (req 20) | Additional `claude -p` call with targeted prompt |
| Regression test check (req 21) | Enhance Track 4 (Tests) prompt |
| Rollback plan check (req 22) | Add to review prompt for risky file paths |
| Comment triage FIX/IGNORE (req 24) | Single `claude -p --json-schema` call for structured output |
| Fix agents (req 27) | `claude -p --permission-mode edit` with scoped fix prompt |

#### THIN WRAPPER (<30 lines each)

| Requirement | What to build |
|---|---|
| Arg parsing (req 2) | Clone `ralph-wiggum-v2.sh` skeleton, add `--resume`/`--force` |
| Non-Python check (req 9) | `gh pr diff --name-only \| grep -q '\.py$'` + exit logic |
| Size/complexity check (req 10) | `gh pr view --json additions,deletions,changedFiles,body` + threshold logic |
| CI config scrutiny (req 11) | `gh pr diff --name-only \| grep -E 'workflows/\|Dockerfile\|Makefile'` |
| Aggregate/filter findings (req 16) | `jq` to filter `.severity == "blocker"` and dedupe by `(file, line)` |
| Fetch owned comments (req 23) | `gh api graphql` for `reviewThreads` + filter by marker prefix |
| Post-fix scope validation (req 27b) | `git diff --name-only` + `git checkout -- <out-of-scope>` |
| Log dir + state.json (req 34) | Follow ralph-wiggum log pattern + write JSON state |

#### BUILD (custom implementation required)

| Requirement | What to build | Est. size |
|---|---|---|
| Diff coordinate validation (req 16a) | Parse unified diff hunks, extract valid `(file, line)` ranges, reject invalid findings | ~50 lines Python |
| Comment fingerprint hashing (req 17) | Hash `(file, line, normalized_explanation)` for dedup across runs | ~20 lines |
| Resume state machine (req 34, `--resume`) | Read/write `state.json`, resume from last completed phase | ~30 lines |

### Structured Output Decision

Review agents shall use Claude's `--json-schema` flag instead of ad-hoc JSON Lines instructions where possible. This enforces the output schema at generation time and reduces parsing failures. The finding schema:

```json
{
  "type": "object",
  "properties": {
    "severity": { "enum": ["blocker", "suggestion"] },
    "file": { "type": "string" },
    "line": { "type": "integer" },
    "start_line": { "type": ["integer", "null"] },
    "side": { "enum": ["RIGHT", "LEFT"], "default": "RIGHT" },
    "explanation": { "type": "string" }
  },
  "required": ["severity", "file", "line", "explanation"]
}
```

Note: `--json-schema` produces a single JSON object. For multiple findings per track, the schema wraps findings in `{ "findings": [...] }`.

### Tools Evaluated but Not Selected as Primary

| Tool | Evaluation |
|---|---|
| **GitHub Copilot Code Review** | Cannot use custom review guidelines (§A-§J), no blocker/suggestion severity model, no structured output, no triage/fix loop capability. Useful as supplemental signal but not the core engine. |
| **CodeRabbit / Greptile** | Could replace Phase 1 review but adds SaaS dependency and less control over blocker-only policy and finding format. |
| **Danger.js** | Good for deterministic policy checks (empty body, oversized PR) but adds Node.js dependency to a bash-native workflow. |
| **Copilot Coding Agent** | Opens separate PRs instead of fixing in-place; wrong model for iterative fix loop on the same branch. |
| **reviewdog** | Useful for converting lint/type-check output to PR annotations, but review tracks already produce their own findings. |

### Architecture: Local Script Now, GitHub Actions Later (Deferred)

**Current implementation:** Option B — local `pr-iteration.sh` as specified in this PRD.

**Deferred migration to GitHub Actions (Option A):** Each phase shall be implemented as an independent function with clean inputs/outputs so that future migration to GitHub Actions is straightforward. In the Actions architecture:

- Phase 0 (validation) becomes a single job producing outputs for downstream jobs.
- Phase 1 (review) becomes a **matrix job** — Actions' `strategy.matrix` runs the same job template 5 times in parallel, one per review track. Each track gets its own runner, its own `claude -p` call, and saves findings as an artifact.
- Phases 2-3 (triage + fix) become a downstream job that downloads all 5 track artifacts and runs the triage/fix loop.
- Logs/state move from local `.pr-iteration-logs/` to GitHub Actions artifacts.
- Fork safety, permissions, retry, and timeout policies are handled natively by Actions.

This migration is **out of scope for the initial implementation** but the function-based design shall not preclude it.

### Estimated Custom Code

| Component | Lines | Language |
|---|---|---|
| `pr-iteration.sh` orchestrator | ~200 | Bash |
| Prompt templates (5 tracks + triage + fix + critical path) | ~150 | Markdown |
| Diff coordinate validator | ~50 | Python |
| Comment fingerprint hasher | ~20 | Bash/Python |
| JSON aggregation/filtering | ~40 | jq/Bash |
| **Total** | **~460 lines** | — |

## Resolved Questions

1. **GraphQL vs REST for thread resolution** — Confirmed: `gh api graphql` fully supports the `resolveReviewThread` mutation. Thread IDs (`PRRT_...` Node IDs) are obtained by querying `pullRequest.reviewThreads` via GraphQL. This approach is already specified in the Technical Considerations section.
2. **Review comment context** — Defer to GitHub Copilot's PR review capabilities. The review agents will leverage Copilot's existing intelligence to determine appropriate context; the PRD does not prescribe diff-only vs full-file.
3. **Commit strategy** — One commit per iteration. All fixes within a single iteration are grouped into a single commit (`fix: address review comments (iteration N)`), as specified in req 29.
4. **Tooling delegation** — Analyzed by Claude Opus 4.7 and GPT-5.4. ~80% of requirements delegate to existing tools (`gh` CLI, `claude -p`, `git`, existing review framework). Only diff coordinate validation requires fully custom implementation. See "Design Decisions — Tooling & Delegation" section above.
5. **Structured output format** — Use Claude's `--json-schema` flag instead of ad-hoc JSON Lines for review findings and triage decisions. This enforces schema at generation time.
6. **GitHub Actions migration** — Deferred. Local script is the initial implementation. Each phase shall be an independent function to enable future migration to Actions matrix jobs. See "Architecture: Local Script Now, GitHub Actions Later" above.
