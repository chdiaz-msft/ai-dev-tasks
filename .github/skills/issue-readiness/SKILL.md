---
name: issue-readiness
description: >
  Validate that a GitHub parent issue and its child issues are ready for autonomous
  implementation with the Local Issue Runner.
  Use when asked whether an issue is ready to run, ready for the implementer,
  sufficiently specified, correctly decomposed, or safe to start through the runner.
---

# Issue Readiness

Determine whether a GitHub parent issue and its child issue graph can be handed to the Local Issue Runner without requiring implementation agents to rediscover product intent, invent missing decisions, or begin from an invalid execution environment.

This is a read-only preflight.
Do not edit issues, branches, configuration, or repository files unless the user separately asks for remediation.

## Inputs

Require a GitHub parent issue URL or an unambiguous repository and issue number.

For a complete ready-to-run verdict, also require:

- The target repository clone.
- The remote feature branch.
- The Local Issue Runner configuration file, or enough information to construct a temporary configuration outside the repository.
- Any endpoint, deployment, tenant, or authentication prerequisites needed by the feature.

If execution inputs are unavailable, continue with issue analysis but report execution readiness as `UNVERIFIED`.
Never call the issue fully ready to run when a required gate is unverified.

## Verdicts

Report one verdict for each gate:

- `PASS`: The gate is supported by direct evidence.
- `WARN`: The gate is usable, but there is a concrete non-blocking risk.
- `FAIL`: The gate contains a blocker.
- `UNVERIFIED`: Required evidence was unavailable or could not be retrieved safely.

The overall verdict must be one of:

- `READY`: Every required gate passes.
- `READY WITH WARNINGS`: Every required gate passes and one or more non-blocking warnings remain.
- `NOT READY`: At least one required gate fails.
- `INCOMPLETE CHECK`: No gate fails, but at least one required gate is unverified.

Warnings must never hide missing information that an implementation agent would need to make a material product or technical decision.
Such omissions are blockers.

## Workflow

### 1. Establish the target

1. Resolve the parent issue, repository, target clone, feature branch, and configuration path.
2. Confirm the issue is open.
3. Confirm the repository and branch refer to the intended implementation target.
4. Read the complete parent body and comments.
5. Read the complete body and comments of every child issue.
6. Read linked specifications, PRDs, ADRs, and maintained design documents that define acceptance behavior.
7. Read the target repository's contributor instructions, domain glossary, and applicable ADRs before judging terminology or implementation seams.

Do not infer missing requirements from issue titles.
Do not treat stale comments as authoritative when a later issue-body edit or maintained specification supersedes them.
Record unresolved contradictions explicitly.

### 2. Validate the GitHub issue graph

From this repository's root, minimally invoke the command help before relying on the command form:

```powershell
uv run python -m local_issue_runner validate-parent --help
```

Then run:

```powershell
uv run python -m local_issue_runner validate-parent <parent-issue-url> --json
```

The graph gate fails when any of these conditions exists:

- The parent is closed.
- A configured child is closed or outside the parent scope.
- Native sub-issue membership is incomplete or ambiguous.
- A dependency points outside the feature scope without an explicit external prerequisite.
- A dependency cycle exists.
- A child belongs to more than one configured parent.
- No issue is ready even though unfinished work remains.
- The runner reports any structural finding.

Preserve the validator's suggested issue changes in the final report.

The validator proves graph structure only.
It does not prove that issue content or the local execution environment is ready.

### 3. Validate the parent contract

The parent must provide a stable product contract for the complete feature.

Require:

- A concrete problem statement describing who is affected and what cannot currently be accomplished.
- An externally observable feature outcome.
- Explicit scope boundaries and meaningful exclusions.
- Product and technical decisions that children may rely on without reopening design.
- A coherent definition of feature completion.
- A validation strategy covering deterministic tests and any required live qualification.
- Privacy, security, data-handling, migration, and compatibility constraints when applicable.
- Links to authoritative maintained documents when the issue intentionally delegates detail.

Fail the parent gate when it is merely a theme, backlog bucket, implementation brainstorm, or collection of unrelated changes.

### 4. Validate every child issue

Evaluate every child independently.
Do not assume details from another child unless a dependency edge and authoritative shared contract make that dependency explicit.

Each child must contain:

- A single clear outcome.
- The user-visible, operator-visible, or developer-visible behavior it delivers.
- Objective acceptance criteria.
- A practical test seam or verification method.
- Explicit non-goals when adjacent behavior could reasonably be mistaken as included.
- Enough domain context and constraints to avoid inventing requirements.
- A dependency list that agrees with the native GitHub dependency graph.
- Scope that can reasonably be completed in one fresh implementation-agent context.

Acceptance criteria must describe observable behavior rather than only file edits, class names, or private implementation steps.
Implementation guidance may constrain the solution, but it cannot replace an acceptance contract.

Fail a child when any of these conditions exists:

- Material behavior is described with terms such as "properly", "support", "handle", "clean up", or "as needed" without measurable meaning.
- The implementer must choose among materially different product behaviors.
- The implementer must invent a public contract, data policy, error behavior, migration strategy, or security boundary.
- Acceptance depends on undocumented tribal knowledge.
- The child combines multiple independently deliverable outcomes without a strong atomicity reason.
- The child is a horizontal layer that cannot remain green or demonstrate value independently, unless it is an explicitly justified expand-contract migration step.
- The child requires code from another issue but lacks the matching dependency edge.
- The child duplicates or contradicts another child.
- The named test seam does not observe the required behavior.
- Completion cannot be distinguished objectively from partial implementation.

### 5. Validate decomposition and sequencing

Judge the child set as a task graph rather than as an ordered checklist.

Require:

- Every edge represents a real prerequisite.
- Independent children remain independent and can run concurrently.
- Shared contract changes precede consumers.
- Expand-contract work keeps the branch valid between migration steps.
- Integration and live-qualification issues depend on all capabilities they exercise.
- The final child or parent completion condition proves the assembled feature, not only isolated parts.
- Removing any child would leave a clearly identifiable parent acceptance requirement unmet.

Flag unnecessary serialization as a warning.
Flag missing sequencing that can produce conflicting or invalid parallel work as a blocker.

### 6. Validate the implementation baseline

Inspect the remote feature branch, not only uncommitted local state.

Require:

- The configured remote feature branch exists.
- The target clone resolves to the configured GitHub repository.
- The feature branch starts from the intended baseline.
- Dependency manifests and lockfiles reference packages and paths that exist.
- Required generated artifacts are present or reproducibly generated.
- The baseline validation commands can start and complete successfully before feature work begins.
- Existing failures are either repaired or explicitly isolated by a repository-approved validation strategy.
- The runner-owned worktree and branch naming are compatible with Git and GitHub API handling.

Do not accept `--no-sync`, skipped tests, narrowed test selection, or success-shaped fallbacks merely to bypass a broken baseline.
Such alternatives are acceptable only when they are the repository's documented normal validation path and still cover the intended changes.

Report unrelated local modifications separately.
Treat them as blockers when runner operations could overwrite, merge through, or otherwise interfere with them.

### 7. Run Local Issue Runner compatibility checks

When a configuration file is available, minimally invoke the command help:

```powershell
uv run python -m local_issue_runner doctor --help
```

Then run:

```powershell
uv run python -m local_issue_runner doctor --config <config-path>
```

If `doctor` passes, minimally invoke the plan command help and preview the graph:

```powershell
uv run python -m local_issue_runner plan --help
uv run python -m local_issue_runner plan --config <config-path>
```

Do not run `local_issue_runner run` during readiness validation.

The execution gate fails for:

- Missing or unsupported tools.
- Authentication failures.
- Repository identity mismatches.
- Missing remote branches.
- Unreadable effective branch policy.
- Validation commands that cannot start or do not pass.
- Invalid or unsafe runner state.
- A plan that disagrees with the expected ready frontier.

Distinguish runner defects from target-repository defects.
Provide evidence and a remediation for each, but both block a ready-to-run verdict.

### 8. Validate runtime dependencies

For features that invoke external services or language models, verify prerequisites at the outset.

Check only presence and usability.
Never print credential values, tokens, secrets, connection strings, personal identifiers, or tenant details.

Require, as applicable:

- Endpoint and deployment configuration.
- A supported authentication method.
- A current authenticated session.
- Synthetic or otherwise approved test data.
- Network access.
- Required feature flags and safe test controls.
- A documented live-validation command.

Run a minimal safe invocation only when the user has authorized it and the invocation cannot mutate production data.
Otherwise mark live invocation as unverified and state exactly what remains to be tested.

### 9. Produce the report

Lead with the overall verdict.

Use this format:

```markdown
## Overall verdict: READY | READY WITH WARNINGS | NOT READY | INCOMPLETE CHECK

| Gate | Verdict | Evidence |
| --- | --- | --- |
| GitHub graph | PASS | ... |
| Parent contract | PASS | ... |
| Child implementability | FAIL | ... |
| Decomposition and sequencing | PASS | ... |
| Repository baseline | FAIL | ... |
| Runner compatibility | FAIL | ... |
| Runtime prerequisites | UNVERIFIED | ... |

### Ready frontier

- #123 — Title

### Blockers

1. **Short blocker name**
   Evidence: ...
   Required remediation: ...

### Warnings

- ...

### Suggested issue changes

- #123: Replace or add ...

### Commands checked

- `command` — observed result
```

Omit empty sections.
Use fully qualified issue references for repositories other than the current repository.
Do not say an issue is ready based only on a valid dependency graph.

## Remediation standard

Every blocker must include:

- The affected parent, child, branch, configuration, or environment.
- Direct evidence.
- Why it prevents autonomous implementation.
- The smallest complete remediation.
- Whether the remediation belongs in GitHub, the target repository, runner configuration, credentials, or the Local Issue Runner itself.

Do not rewrite issues automatically.
When the user asks for fixes, preserve established product decisions and modify only the deficient portions.
