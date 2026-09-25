# Rubber Duck Review: Autonomous PR Flywheel Spec

**Spec reviewed:** `tasks/claude-review-cycle/spec.md`

## Critical / Blocking Issues (5)

### 1. Reusable workflow cannot access orchestrator files
**Spec lines:** 230-257, 303-366
Workflow checks out the **consumer PR head** only — it won't contain `orchestrator/` or `prompts/` from the template repo. Day-1 workflow fails immediately.
**Fix:** Package orchestrator as a composite action or Python package, or checkout the template repo into a separate path.

### 2. State persistence is broken / contradictory
**Spec lines:** 134-136, 317-322, 353-357
Cache restore key is `flywheel-state-pr-N`, but save key is `flywheel-state-pr-N-round-I`. Without `restore-keys`, later runs won't find saved state. GitHub cache is also immutable — not a reliable mutable store.
**Fix:** Use one authoritative mechanism (PR comments, artifacts, or cache with `restore-keys` prefix matching). Handle cache misses explicitly.

### 3. `check_suite` event breaks workflow assumptions
**Spec lines:** 288, 293, 413-414
`check_suite` events don't expose `github.event.pull_request` the same way `pull_request` events do. Workflow references to `.pull_request.number` and `.draft` will fail or produce invalid keys.
**Fix:** Add event normalization step or split event handlers.

### 4. "Stable issue IDs" are not actually stable
**Spec lines:** 94-97, 601-607
Sequence-based IDs (`correctness-001`) will shift as findings reorder. Resolution logic marks issues resolved if their exact ID is absent — causes false resolutions and duplicate tracking.
**Fix:** Generate deterministic IDs from reviewer + file path + line/function + issue fingerprint.

### 5. Commit step runs regardless of actual file changes
**Spec lines:** 370-379, 631-635
`has_changes` means "there are actionable issues," not "the agent changed files." Commit step may create empty commits or push unauthorized changes.
**Fix:** Check `git diff --quiet` after fix step; validate changed paths against allowlist before push.

## High Severity Issues (7)

### 6. Human rejection detection never triggers
Code checks `state["issues"]` for `CHANGES_REQUESTED`, but human reviews are in `signals["human_reviews"]` and never merged into `state["issues"]`.
**Fix:** In `flywheel_controller.py main()`, after loading signals, scan `signals["human_reviews"]` for any entry with `"state": "CHANGES_REQUESTED"` and set `state["termination"] = "blocked"` before calling `decide()`. The check in `decide()` at line 676 should read from the signals dict passed as a new parameter, not from `state["issues"]`.

### 7. "All checks green" is specified but not implemented
`decide()` ignores CI/check conclusions entirely — may post `flywheel/merge-ready` while tests are failing.
**Fix:** Pass `signals["checks"]` into `decide()`. Before returning the `"ready"` state, verify all required checks have `"conclusion": "success"`. If any check is failing or pending, return `"waiting"` with a reason listing the failing checks. Add a `required_checks` config option so repos can specify which check names must pass.

### 8. Concurrency model can apply obsolete fixes
`cancel-in-progress: false` means stale runs reviewing old diffs can push fixes after newer commits.
**Fix:** Change to `cancel-in-progress: true`. The spec already says "always new commits, never amend" — so cancelling a stale run is safe; the next push retriggers a fresh round anyway. The comment on line 448 ("queue instead of clobbering") contradicts line 289 which already says `true`; reconcile both to `true` and update the prose.

### 9. Check-suite trigger creates recursive loops
Publishing `flywheel/merge-ready` check can retrigger the workflow, burning Actions minutes.
**Fix:** The `if:` guard on line 294 already filters `check_suite` events from `flywheel-bot`, but the app slug must match exactly. Ensure the GitHub App's slug is `flywheel-bot`. Additionally, add the flywheel's own check name to the filter: `github.event.check_suite.check_runs[0].name != 'flywheel/merge-ready'`. As a belt-and-suspenders measure, add a step that checks for a `flywheel:iter-N` label and skips if `current_round` hasn't changed.

### 10. Fork PR security model is underspecified
PRs from forks won't have trusted secrets. Running PR-controlled code with write-capable app tokens is dangerous.
**Fix:** The `skip if fork PR` step (line 323) uses `exit 0` which silently succeeds — downstream steps still run. Change it to `exit 78` (neutral) or use `if: github.event.pull_request.head.repo.full_name == github.repository` as a job-level condition so the entire job is skipped for forks. Also gate the `pull_request_target` event (not `pull_request`) if fork support is ever needed, ensuring code runs from the base branch.

### 11. Prompt injection risk
PR content can contain instructions to ignore rules, leak secrets, or edit forbidden paths. Spec relies mostly on prompt-level guards, not hard enforcement.
**Fix:** Layer hard enforcement outside the AI agent: (1) the path-allowlist validation step (line 407) already exists — make it a **blocking gate** that fails the job (not just reverts files) if forbidden paths are touched; (2) run `git diff --name-only` post-fix and reject the commit if any path outside the allowlist was modified; (3) strip or sanitize PR body/comment content before passing it to reviewer prompts (e.g., truncate to N chars, remove markdown code fences that could embed instructions); (4) use a separate read-only token for the review step and only escalate to the write token for the commit step.

### 12. Branch protection guidance weakens human gate
"Dismiss stale approvals on push: OFF" means code can materially change after human approval and still auto-merge.
**Fix:** Turn "Dismiss stale approvals on push" **ON**, and have the flywheel request a fresh human review after its final fix round by posting a comment tagging the original approver. This is safer than the current approach. To prevent the "every flywheel commit invalidates approval" problem mentioned in the spec, scope the dismissal rule: use a Repository Ruleset with a path condition so only changes to sensitive paths (e.g., `.github/`, `infra/`) dismiss stale approvals, while changes to `src/` and `tests/` do not.

## Medium Severity Issues (6)

### 13. Reviewer timeout hides missing coverage
If security reviewer times out, loop proceeds and may mark success without security review.

### 14. Code scanning API usage is likely incorrect
`get_codescan_alerts(state="open", ref=pr.head.ref)` has permission/branch/pagination issues.

### 15. Review thread resolution requires GraphQL
REST `get_review_comments()` can't reliably determine unresolved threads. Need GraphQL `reviewThreads { isResolved }`.

### 16. Merge-ready check may become stale on old SHAs

### 17. "5-minute minimum poll interval" rule is not implemented

### 18. Claude Code Action integration is underspecified
Unclear if `prompt_file` supports `{{templating}}`, whether the action commits itself, and how outputs are consumed.

## Low Severity / Design Concerns (3)

### 19. "Free / GHEC-native" claim is overstated — Claude API is a paid third-party service.

### 20. Scalability/cost controls are too light for org-wide rollout.

### 21. "Ship today" framing is unrealistic — core pieces are pseudocode (`_call_reviewer_api`, dedupe, path enforcement).

## Recommendation

The architecture is promising but the spec has **5 blocking issues** that would prevent it from working at all on day 1. The highest-leverage fixes are:

1. Solve the template-repo-vs-consumer-repo file access problem (package as composite action)
2. Fix state persistence (use `restore-keys` prefix or switch to artifacts)
3. Normalize event payloads across trigger types
4. Make issue IDs deterministic (content-hash-based)
5. Gate commits on actual `git diff`, not decision intent
