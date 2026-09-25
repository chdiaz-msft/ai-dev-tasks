# PRD: PR Split — Copilot Agent Skill

## Introduction / Overview

Large pull requests are one of the biggest bottlenecks in code review. They take longer to review, produce lower-quality feedback, and increase the risk of bugs slipping through. This feature introduces a **Copilot agent skill** that intelligently breaks a large PR into multiple smaller, stacked PRs — each focused on a single logical concern and independently reviewable.

The skill analyzes the PR's diff and commit history, proposes a split plan, validates it, and then executes it by creating stacked branches and PRs via the `gh` CLI. It follows the existing `.github/skills/` pattern used by the `python-code-review` skill.

## Goals

1. **Reduce PR review time** — smaller, focused PRs are faster and easier to review. Each child PR is focused and reviewable in stack order (its diff against the immediate parent branch is coherent and addresses a single concern).
2. **Improve review quality** — reviewers can focus on one concern at a time instead of context-switching across unrelated changes.
3. **Preserve change relationships** — stacked PRs maintain the dependency chain so changes land in the correct order.
4. **Automate the tedious work** — creating branches, cherry-picking files, opening PRs, and linking them is manual and error-prone; the skill handles it end-to-end.
5. **Be language-agnostic** — work on any repository regardless of language or framework.

## User Stories

- **As a developer**, I want to invoke the PR split skill on a large PR so that it is automatically broken into smaller, reviewable PRs without me having to manually create branches and cherry-pick changes.
- **As a code reviewer**, I want to review focused, single-concern PRs so that I can give higher-quality feedback in less time.
- **As a team lead**, I want a shared workflow my team can use to enforce smaller PRs so that our review process stays efficient and consistent.
- **As a developer**, I want the split to be validated before execution (dry-run) so that I can review and approve the plan before any branches or PRs are created.
- **As a developer**, I want the skill to detect when a PR cannot be safely split at file granularity so that I'm warned before ending up with broken child PRs.

## Functional Requirements

### Skill Interface

1. The skill shall be implemented as a Copilot agent skill at `.github/skills/pr-split/SKILL.md`, following the existing SKILL.md frontmatter format (`name`, `description`).
2. The skill shall accept a PR number as input (provided by the user or detected from the current branch via `gh pr view --json number`).
3. The skill shall be language-agnostic — it must not depend on any language-specific tooling or review guidelines.

### Phase 0 — Preflight Checks

4. The skill shall verify the local worktree is clean (`git status --porcelain` is empty).
5. The skill shall verify `gh` CLI is authenticated (`gh auth status`).
6. The skill shall verify the target PR exists and is open (`gh pr view <num>`).
7. The skill shall verify the PR's head branch is available locally. If not, the skill shall fetch and checkout the branch (`git fetch origin <branch> && git checkout <branch>`). If the checkout fails (e.g., due to uncommitted changes), the skill shall stop and instruct the user to resolve the issue.
8. The skill shall detect and reject PRs originating from forks (v1 limitation).
9. The skill shall check whether the PR branch has merge conflicts with the base branch. If conflicts exist, the skill shall stop and instruct the user to resolve them first.

### Phase 1 — Gather Context

10. The skill shall fetch PR metadata via `gh pr view <num> --json title,body,baseRefName,headRefName,number,labels,milestone,url`.
11. The skill shall gather structured change summaries to avoid context-window limits:
    - `gh pr diff <num> --name-only` for the file list.
    - `git diff --stat <base>...<head>` for the change summary.
    - `git diff --name-status <base>...<head>` for adds/modifies/deletes/renames.
    - `git log --oneline <base>...<head>` for the commit history.
12. The skill shall read targeted per-file diffs only as needed for grouping decisions (not the entire diff at once).

### Phase 2 — Choose Split Strategy

13. The skill shall analyze the commit history and choose one of three strategies:
    - **Commit-based split** — when commits are already logically clean and separable (each commit addresses a single concern). Preferred because it preserves history.
    - **File-based split** — when commits are messy/mixed but files can be cleanly grouped by concern.
    - **Not safely splittable** — when individual files contain changes for multiple unrelated concerns (mixed hunks). The skill shall warn the user and recommend manual intervention for those files.
14. The skill shall explain its strategy choice and rationale to the user.

### Phase 3 — Analyze & Propose Groupings

15. The skill shall analyze the changes and propose logical groups. Grouping heuristics include (but are not limited to):
    - Infrastructure/config changes (CI, build, deps) separate from application code.
    - Refactoring/cleanup separate from new feature code.
    - Database/schema/migration changes before application logic.
    - Shared utilities/helpers before feature-specific code that depends on them.
    - Tests grouped with their corresponding implementation.
    - Documentation changes as standalone or grouped with related code.
16. For each proposed group, the skill shall output:
    - **Name** — a short descriptive label (e.g., `refactor-auth-module`).
    - **Description** — what this group does and why it is separate.
    - **Files** — list of files included, with change type (A/M/D/R).
    - **Rationale** — why these files belong together.
    - **Dependencies** — which other groups must come before this one.
17. The skill shall use heuristic dependency ordering based on file path patterns and change types: config/build files before application code, migrations/schema before model code, shared utilities before feature code. The skill shall NOT attempt language-specific semantic analysis (e.g., parsing import graphs, ASTs, or type systems). When ordering is uncertain, the skill shall flag the ambiguity in the plan for user review.

### Phase 4 — Validate Split Plan (Dry Run)

18. The skill shall validate the split plan before executing:
    - Every file in the original diff appears in exactly one group (accounting for renames, deletes, mode changes).
    - No group is empty.
    - Dependency ordering is acyclic (can be topologically sorted).
    - Proposed branch names (`split/<original-branch>/<N>-<group-name>`) do not collide with existing branches.
    - For commit-based splits, the file set produced by cherry-picking the selected commits matches the intended file set for the group. If extra or missing files are detected, the skill shall warn the user and suggest switching to file-based strategy for that group.
19. The skill shall present the full plan to the user, including:
    - Group order (stack sequence).
    - Branch names and target branches for each PR.
    - Files and change types per group.
    - Risks or warnings detected (e.g., files with mixed concerns, large groups).
20. **The skill shall require explicit user confirmation before proceeding to execution.** This is a hard gate — the skill must not create branches or PRs without approval. During confirmation, the user may: (a) approve the plan as-is, (b) reject the plan entirely, or (c) request revisions by describing desired changes (e.g., move files between groups, rename groups, reorder). The skill shall regenerate the plan incorporating the user's feedback and present it again for confirmation. There is no limit on revision rounds.

### Phase 5 — Execute the Split

21. For each group in dependency order, the skill shall:
    a. Create a new branch from the previous group's branch (or the base branch for group 1).
    b. Apply the changes using the chosen strategy:
       - **Commit-based:** Cherry-pick the relevant commits.
       - **File-based:** `git checkout <original-branch> -- <file1> <file2> ...` then commit. For file-based splits, the skill shall preserve the original commit's author (`--author`), and copy `Co-authored-by`, `Signed-off-by`, and issue-reference trailers from the original commits into the new commit message.
    c. Validate the group by running build/test commands using the following discovery order: (1) user-provided commands (if specified during plan confirmation), (2) well-known package scripts (`npm test`, `make test`, `cargo test`, etc.), (3) CI config file commands (`.github/workflows/`, `.circleci/`, etc.). If no commands are discovered, skip validation with a warning. Build/test validation may be disabled by the user. If validation fails, stop and report the issue — do not continue with broken groups.
    d. Push the branch to the remote.
    e. Open a PR via `gh pr create` with:
       - Title: `[N/M] <group-name>: <description>`.
       - Body: group description, "Part N of M — split from #original_pr", and a stack diagram showing all PRs in the chain.
       - Target: previous group's branch (or base branch for group 1).
       - Labels and milestone copied from the original PR.
22. After all child PRs are created, the skill shall:
     a. Present a summary of all created PRs with links.
     b. Convert the original PR to draft (`gh pr ready --undo`) and add a `split-in-progress` label to prevent accidental merge while child PRs are active.

### Phase 6 — Close Original PR

23. **The skill shall require explicit user confirmation before closing the original PR.** This is a second hard gate.
24. Upon confirmation, the skill shall:
    a. Post a summary comment on the original PR listing all child PRs with numbers, titles, links, and the stack order.
    b. Close the original PR via `gh pr close`.

### Error Recovery / Rollback

25. If the split fails partway through execution, the skill shall provide rollback instructions:
    - Delete local branches created by the split.
    - Delete remote branches that were pushed.
    - Close any child PRs that were opened.
    - Restore the original branch checkout.
26. The skill shall track completed steps by posting a progress comment on the original PR (via `gh pr comment`) after each group is successfully pushed and its child PR opened. This comment serves as durable state: if the skill is interrupted, it can read back these comments to determine which groups have been completed and resume from the next group.

## Non-Goals (Out of Scope)

- **Hunk-level splitting** — v1 operates at file or commit granularity. Splitting individual hunks within a file is not supported; the skill warns when this would be needed.
- **Fork-origin PRs** — PRs from forks require pushing to a different remote and are not supported in v1.
- **Auto-rebase of downstream stack** — if an earlier PR in the stack is amended after creation, the skill does not automatically rebase later PRs.
- **CI/CD integration** — this is invoked by a developer via Copilot, not as a GitHub Action.
- **Language-specific validation** — the skill does not run language-specific linters or type checkers; it uses whatever build/test commands are available in the repo.
- **Language-specific dependency graph analysis** — the skill uses file-path heuristics for ordering, not import parsing, ASTs, or type systems.
- **GPG signature preservation** — file-based splits create new commits that cannot preserve original GPG signatures.
- **Interactive hunk selection** — the skill does not present an interactive UI for selecting which hunks go where.

## Technical Considerations

- **Dependencies:** Requires `gh` CLI (authenticated) and `git`. No other external tools.
- **Context window management:** The skill gathers structured summaries (file lists, stats, commit log) rather than dumping the full diff into context. Per-file diffs are read only as needed.
- **Stacked PR merge complexity:** Stacked PRs require merging in order (PR 1 first, then PR 2, etc.). The skill should document this in the child PR descriptions. If PR 1 is amended, downstream PRs may need rebasing — this is a known limitation.
- **Branch naming:** Deterministic branch names (`split/<original-branch>/<N>-<group-name>`) make it easy to identify and clean up split branches.
- **GitHub API:** PR creation and closing use `gh pr create` and `gh pr close`. Comment posting uses `gh pr comment`. No direct API calls needed.
- **Existing patterns:** Follows the `.github/skills/` SKILL.md pattern established by `python-code-review`.

## Success Metrics

- Reduces average PR review time by producing smaller, focused PRs.
- All child PRs are focused and reviewable in stack order (each addresses a single concern).
- The split plan accurately groups related changes together.
- Zero manual cleanup needed after a successful split (branches, PRs, and links are all created correctly).
- Developers can confidently approve the dry-run plan before execution.

## Open Questions

1. **Build/test validation per group** — should the skill attempt to run build/tests after each group is applied, or is that too slow for large repos? Should this be configurable?
2. **PR description preservation** — should the original PR's description be distributed across child PRs (each getting the relevant section), or should each child PR link back to the original for full context?
3. **Reviewer assignment** — should the skill copy reviewers from the original PR to each child PR, or let the team's CODEOWNERS / auto-assignment handle it?
4. **Maximum group count** — should there be a limit on how many child PRs the skill creates (e.g., max 5)? Too many small PRs can also be burdensome.
5. **Merge strategy for stacked PRs** — should the skill document a recommended merge strategy (merge commit vs. squash) for stacked PRs in the child PR descriptions?

---

## Appendix: Tool Delegation Map

Research conducted with Claude Opus 4.6 and GPT-5.4 (May 2026) to identify existing tools, plugins, and AI capabilities that can handle each phase of this workflow. **Goal: implement as little as possible and orchestrate existing technology.**

### Net Assessment

- **~80% of the workflow is delegatable** to existing CLI tools + LLM prompting.
- **~20% is custom orchestration** — primarily the SKILL.md prompt that ties phases together, plus ~50–100 lines of deterministic validation logic (Phase 4).
- The "custom code" is primarily **prompt engineering**, not traditional software.

### Phase 0 — Preflight Checks → ✅ Fully Delegatable

**Tools:** `gh` CLI + `git` (no custom code needed)

| Check | Command |
|-------|---------|
| Clean worktree | `git status --porcelain` |
| gh authenticated | `gh auth status` (exits 1 on failure) |
| PR exists / open | `gh pr view <N> --json state,number` |
| Fork check | `gh pr view <N> --json isCrossRepository` |
| Branch available | `git branch --list <name>` / `git fetch origin <branch>` |
| Merge conflicts | `git merge-base` / `git merge-tree` |

**Build: Nothing.** Prompt template instructs the AI to run these commands and interpret exit codes.

### Phase 1 — Gather Context → ✅ Fully Delegatable

**Tools:** `gh` CLI + `git` (no custom code needed)

| Data | Command |
|------|---------|
| PR metadata | `gh pr view <N> --json title,body,baseRefName,headRefName,labels,milestone,commits,files,additions,deletions` |
| File list | `gh pr diff <N> --name-only` |
| Change stats | `git diff --stat <base>...<head>` |
| Name/status | `git diff --name-status <base>...<head>` |
| Commit history | `git log --oneline <base>...<head>` |
| Per-file diff | `gh pr diff <N>` (read selectively to manage context window) |

**Build: Nothing.** Prompt template with structured data gathering instructions.

### Phase 2 — Choose Split Strategy → ✅ Delegatable to LLM

**Tool:** LLM prompting (Copilot / Claude)

Given commit history + file list + change stats from Phase 1, the LLM classifies the PR into one of the three strategies (commit-based, file-based, not safely splittable). This is a classification task that LLMs handle well with proper prompting.

**Build: Prompt template only** (~50 lines of structured instructions + examples).

### Phase 3 — Analyze & Propose Groupings → ✅ Delegatable to LLM

**Tool:** LLM prompting with structured JSON output

The LLM analyzes per-file diffs and proposes groups. Grouping heuristics (infra/config, refactoring, schema, utils, features, tests, docs) map directly to what LLMs excel at — understanding code semantics and purpose.

**Build: Prompt template + JSON output schema.** The LLM generates structured JSON that Phase 4 validates deterministically.

### Phase 4 — Validate Split Plan → ⚠️ Partially Custom

**Tool:** Deterministic validation code (~50–100 lines)

Validation rules are simple set/graph operations best handled by code, not LLM reasoning:
- Every file in exactly one group (set coverage)
- No empty groups (length check)
- Acyclic dependencies (topological sort)
- Branch name collisions (`git branch --list 'split/*'`)
- For commit-based: cherry-pick file set matches intended file set

**Build: ~50–100 lines of Python** (or inline prompt logic where the AI runs the checks step-by-step). User confirmation gate is handled by the skill's conversational flow.

### Phase 5 — Execute the Split → ⚠️ Partially Delegatable

**Recommended approach (tiered):**

#### Option A: `github/gh-stack` (Best — if available)
- **`gh stack link branch1 branch2 branch3 --base main`** — creates stacked PRs from pre-made branches in one call.
- Pushes branches, creates PRs with correct base-branch chaining, creates a native GitHub Stack.
- Works without prior `gh stack init` — designed for tools that manage branches externally.
- Has AI agent skill: `gh skill install github/gh-stack`.
- **Status:** Private preview (waitlist at `gh.io/stacksbeta`). Availability TBD.

#### Option B: Raw `gh pr create` chaining (Fallback)
1. Loop: `git checkout -b split/<branch>/<N>-<name>` → `git checkout <original> -- <files>` → `git commit`
2. `git push origin <branch>` for each
3. `gh pr create --base <previous-branch> --head <current-branch>` for each
4. Manually add stack diagram to PR descriptions

#### Option C: Third-party stack tools (Alternatives)
| Tool | Model | Strengths | Limitations |
|------|-------|-----------|-------------|
| **git-spice** (`gs stack submit`) | Branch-based | Multi-platform (GitHub/GitLab/Bitbucket), active, Go | Extra dependency |
| **Git Town** (`git town propose --stack`) | Branch-based | Mature, explicit `undo`, AI-agent-friendly (v23+) | Extra dependency |
| **ghstack** | Commit-per-PR | Facebook-originated, Python | Can't merge via normal GitHub UI |
| **spr** | Commit-per-PR | Simple, Go | Commit-per-PR only |

**Build:** Branch creation loop + file checkout logic (~30–50 lines of git commands). The stack tool handles PR creation and linking.

### Phase 6 — Close Original PR → ✅ Fully Delegatable

**Tools:** `gh` CLI (trivial — two commands)

```bash
gh pr comment <N> --body "Split into stacked PRs: #X, #Y, #Z (see stack for review order)"
gh pr ready <N> --undo      # Convert to draft
gh pr close <N>
```

**Build: Nothing.**

### Error Recovery / Rollback → ⚠️ Light Custom Logic

**Tools:** `gh` CLI + `git` for cleanup. Track created branches/PRs for rollback.

- `git branch -D <branch>` — delete local branches
- `git push origin --delete <branch>` — delete remote branches
- `gh pr close <N>` — close child PRs
- **Git Town** `git town undo` or **git-branchless** `git undo` if using those tools

**Build:** Progress tracking (which branches/PRs were created) so rollback knows what to clean up. Can be done via `gh pr comment` on the original PR as durable state.

### Post-Split Maintenance (Bonus)

**Tool: `gh-domino`** ([github.com/134130/gh-domino](https://github.com/134130/gh-domino))
- Auto-detects merged PRs in a stack and rebases remaining PRs
- Zero configuration, works with all GitHub merge strategies
- `gh domino --auto` for unattended operation

### Recommended Architecture

```
SKILL.md (Copilot Agent Skill)
  │
  ├── Phase 0–1: Prompt → AI runs gh/git commands → structured data
  ├── Phase 2–3: Prompt → AI analyzes data → JSON split plan
  ├── Phase 4:   Prompt → AI runs deterministic validation on plan
  ├── Phase 5:   Prompt → AI creates branches → gh stack link (or gh pr create)
  └── Phase 6:   Prompt → AI runs gh pr comment + gh pr close
```

The entire skill is a **well-structured prompt** guiding the AI through the workflow using `gh` and `git` as tools. The AI itself is the orchestrator.

### Key Tools Reference

| Tool | URL | What It Does | Status |
|------|-----|-------------|--------|
| `github/gh-stack` | github.com/github/gh-stack | Official GitHub stacked PR extension | Private preview (2026) |
| `gh` CLI | cli.github.com | PR/repo/auth operations | Stable, official |
| `git-spice` | github.com/abhinav/git-spice | Stacked branch CLI (GitHub/GitLab/Bitbucket) | Active, Go |
| `Git Town` | github.com/git-town/git-town | Git workflow automation with undo | Mature, Go |
| `gh-domino` | github.com/134130/gh-domino | Auto-rebase stacked PRs post-merge | Active, Go |
| `ghstack` | github.com/ezyang/ghstack | Commit→PR mapping (Facebook) | Active, Python |
| `spr` | github.com/ejoffe/spr | Commit→PR mapping | Active, Go |
| `git-branchless` | github.com/arxanas/git-branchless | Patch-stack manipulation, `git split` (unreleased) | Alpha, Rust |
| Graphite | graphite.dev | Full stack workflow | Commercial SaaS |
