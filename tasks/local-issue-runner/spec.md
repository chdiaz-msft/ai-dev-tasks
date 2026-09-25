# Local Issue Runner

**Status:** Proposed specification, ready to split into implementation tickets.
**Date:** 2026-09-18.
**Scope:** A Windows-first Python program for one developer, one local machine, and one GitHub repository per invocation.

## 1. Problem and intended outcome

I already have a spec and agent-ready GitHub tickets created through Matt Pocock's framework.
Several parent issues group those tickets into features, and each feature has a branch that will eventually merge into main.
I want to leave a local program running that steadily implements eligible tickets, responds to review findings, gets every PR reviewed by my existing auto-reviewer, merges approved work, and closes the corresponding issues.

The program should remove repetitive coordination, not become another planning agent.
It must be easy to inspect, interrupt, and restart without losing work or accidentally repeating a merge.
It is a trusted local development tool, not a hosted platform or a security sandbox.

## 2. Opinionated decisions

| Concern | Decision |
| --- | --- |
| Runtime | Python 3.11 or newer, managed with `uv` |
| User interface | A foreground CLI with readable console output |
| Scheduling | Poll immediately, then every five minutes by default |
| Intelligence | Deterministic scheduling; coding agents implement and repair |
| Concurrency | One coding-agent or reviewer subprocess at a time |
| Work in progress | At most one runner-managed child PR per parent |
| GitHub access | Use authenticated `gh` commands and structured JSON |
| Persistence | One local SQLite database plus per-job logs |
| Configuration | One TOML file plus an environment variable for the reviewer |
| Autonomy | Start with PR creation only; child and parent merging require explicit promotion |
| Branch updates | Merge the latest base into an existing PR branch; never rebase published branches |
| Child merge strategy | Squash into the parent's feature branch |
| Parent merge strategy | Merge commit into main, retaining feature-branch ancestry |
| Review | The external auto-reviewer must explicitly approve the current review input |
| Finalization | Close a parent only after its feature has actually merged into main |
| Cleanup | Retain worktrees and branches; deletion is an explicit human operation |

There is no database server, message broker, web dashboard, webhook listener, distributed lock, or agent fleet.
There is no requirement to use an LLM for an idle polling cycle.
Version one deliberately trades throughput for predictable Git history and easy recovery.

Autonomy is deliberately staged:

1. `create_pr` is the default and lowest level.
   The runner may implement, validate, push, create or repair PRs, and obtain review, but it must never merge a PR or close an issue as completed.
   After a human merges a child PR, the runner waits for the human to close the child issue before treating it as complete and starting work that depends on that completion.
2. `merge_children` adds automatic child-PR merging and child issue closure after all gates pass.
3. `merge_parents` adds automatic integration-PR merging and parent issue closure after all gates pass.

Moving to a higher level requires an explicit configuration change and a valid live-qualification receipt for the installed toolchain.
A lower autonomy level must make prohibited transitions unavailable in the decision and execution layers rather than relying only on a prompt or operator convention.

## 3. User stories

1. As the developer, I can configure parent issue numbers, their feature branches, and the main branch.
2. As the developer, I can check configuration and authentication without starting paid coding or review work.
3. As the developer, I can preview the next action without changing GitHub or my repository.
4. As the developer, I can leave the runner open and have it discover remaining child issues.
5. As the developer, I can rely on blocking relationships to determine implementation order.
6. As the developer, I get a separate branch and worktree for each child issue.
7. As the developer, I can let the runner take responsibility for an existing PR explicitly.
8. As the developer, I can have failing tests and actionable review findings sent back to a coding agent.
9. As the developer, I know that an old approval cannot authorize merging newly changed code.
10. As the developer, I can see why a PR is waiting instead of having the runner retry indefinitely.
11. As the developer, I can stop and restart the process without creating duplicate PRs.
12. As the developer, I get a final integration PR before a parent is marked complete.
13. As the developer, I retain logs, commits, and worktrees when a job fails.
14. As the developer, I can pause an issue or the whole runner without deleting its work.

## 4. Domain model and sources of truth

A **parent** is a configured feature-tracking GitHub issue.
A **child** is an implementation issue belonging to exactly one configured parent.
A **feature branch** is the integration branch for a parent's children.
A **work branch** is the branch for a single child PR.
An **integration PR** is the feature-branch-to-main PR for a parent.
A **job** is one bounded invocation of a coding agent or the external reviewer.
A **review receipt** is locally recorded evidence of an external review against a particular PR, head commit, and base commit.
A **blocker** is a reason that prevents progress, with an explicit distinction between waiting on an external event and requiring human intervention.

GitHub is authoritative for issue state, dependency relationships, PR state, branch tips, checks, reviews, and merges.
Git is authoritative for local changes and commit ancestry.
SQLite records ownership, attempts, job identity, receipts, pauses, and recovery information.
Stored snapshots do not override newer GitHub facts.
An agent's success message is never proof that tests passed, a PR exists, or a merge happened.

## 5. Inputs and local setup

The following is a proposed configuration shape, not an invocation of an existing implementation.
Relative filesystem paths resolve from the configuration file's directory, never from an assumed working directory.
Git branch names and `owner/repository` identifiers retain their normal Git and GitHub syntax.

```toml
version = 1
repository = "owner/my-app"
repository_path = '..\my-app'
remote = "origin"
main_branch = "main"
poll_seconds = 300
agent_timeout_seconds = 3600
review_timeout_seconds = 1800
max_repair_attempts = 3
max_jobs_per_run = 20
autonomy = "create_pr"

# Replace these with this repository's actual validation commands.
validation_commands = [
  ["uv", "run", "pytest"],
]

# Additional named checks, beyond requirements enforced by GitHub.
required_checks = []

[[parents]]
issue = 100
feature_branch = "feature/customer-export"

[[parents]]
issue = 200
feature_branch = "feature/report-filters"
# Optional replacement for native GitHub sub-issue discovery.
child_issues = [201, 202, 203]
```

`PR_AUTO_REVIEWER_PATH` must identify the existing `auto_review.py` file.
For the current installation, the example value is `C:\git\fde-pr-auto-reviewer\auto_review.py`.
An optional `PR_AUTO_REVIEWER_PYTHON` identifies the Python executable for a reviewer with a separate environment.
Otherwise, use the runner's interpreter rather than whichever `python` happens to be first on `PATH`.

Store runtime files under `%LOCALAPPDATA%\LocalIssueRunner\<repository-key>`.
The repository key includes the canonical local Git common directory and GitHub repository identity.
Keep the database, logs, job input files, copied review evidence, and worktrees outside the developer's ordinary checkout.
Do not commit credentials or generated runtime state.

Version one supports GitHub.com, existing local clones, existing remote feature branches, and same-repository PRs.
It does not create feature branches automatically or infer a repository from a PR number alone.
The configured remote must resolve to the configured repository.
Different parents must have different feature branches, none of which may be main.

`doctor` must check the repository identity, refs, branch names, tool availability, `gh` authentication, Copilot authentication, reviewer startup, reviewer configuration, and GitHub permissions.
It must verify supported `gh`, Copilot CLI, Python, Git, and reviewer versions and exercise the machine-readable interfaces on which the runner relies.
An unknown major version or an unavailable required structured-output interface blocks mutating commands until compatibility is confirmed.
It must also verify that each configured script can start with its intended interpreter and that command arguments are passed correctly on Windows.
Do not execute the reviewer with `--dry-run` as a supposedly free health check, because that option can still invoke an LLM.
Use help or other non-mutating startup checks instead.
At least one repository-appropriate validation command is required before enabling unattended implementation.

Copilot CLI is the initial coding-agent backend and uses the developer's existing authenticated installation.
A separate model endpoint or API key is not required for this backend.
Any later backend that requires an endpoint and credentials must obtain and smoke-test them before implementation runs are enabled.
Do not print credentials or put them in job prompts.

## 6. Discovering work and respecting dependencies

By default, retrieve the parent's native GitHub sub-issues and paginate all results.
When `child_issues` is present, it is the complete authoritative child list for that parent instead.
Do not combine the two modes or use an LLM to guess issue numbers from prose.
Display the discovery mode and resolved child list in `status`.

Support one parent-to-child level in version one.
Reject nested parents, duplicated children across configured parents, missing issues, and dependency cycles with a specific explanation.
An empty child list is a configuration problem, not evidence that a feature is complete.

Read native GitHub blocking relationships for each child.
Do not infer a dependency from an ordinary issue mention.
If dependency data cannot be read completely, that child is not eligible.
Cross-repository dependencies require human intervention in version one.

An implementation dependency is satisfied only when its completion is verified and its code is available in the prospective child's base branch.
For a sibling, this normally means its PR has merged into the same feature branch.
For another parent's child, closing the issue after a feature-branch merge is not enough.
Its work must reach main and be incorporated into the dependent feature branch before the dependent child starts.
Use commit ancestry to verify availability, not issue state alone.
Check the merged PR's resulting merge or squash commit, not the original work-branch head that a squash merge replaces.
If another parent's work has reached main but is absent from the dependent feature branch, show the missing commit and request a feature-branch update.
Automatic cross-parent branch synchronization is outside version one.

An issue explicitly closed as not planned is an intentional scope exclusion, not an implemented dependency.
Do not run a dependent child until the developer removes that blocking relationship.
A completed child with no verifiable merged PR needs a human decision rather than being assumed implemented.
For legitimate no-code work, an explicit local acknowledgement with a recorded reason may count it as satisfied.
Acknowledgements never override review or merge requirements for a PR that does exist.
If a human closes a parent before verified integration, pause its remaining work rather than reopening it or continuing implementation.

Within a parent, choose an eligible issue that unblocks the most remaining children, then break ties by issue number.
Across parents, use round-robin scheduling and persist the last-served parent.
Apply the same cross-parent fairness to expensive implementation, repair, and review jobs for existing PRs.
Cheap recovery, merge bookkeeping, and issue closure may retain global priority because they finish already-observed work without consuming another paid job.
Existing runner-owned PRs take priority over starting new work, but a PR waiting on CI or a human must not prevent another parent from progressing.
Do not open another managed child PR for a parent until its current one is merged, abandoned by a human, or explicitly released from runner ownership.

## 7. Reconciliation loop

The central operation is `reconcile_once`, which reads current facts, selects at most one actionable transition, executes it, and records the observed result.
A transition can be a coding job, review job, push, PR creation, merge, issue closure, or a durable blocker update.
Do not perform a long chain of mutations from one stale snapshot.

The long-running command repeatedly invokes this operation.
After a successful transition, reconcile immediately so a completed merge can unblock another issue without waiting five minutes.
When there is no immediate action, wait for `poll_seconds`.
Do not replay missed ticks after machine sleep or queue overlapping polls.

The action priority is recovery of an interrupted transition, completion bookkeeping for confirmed merges, progress on existing PRs, parent integration, and finally new child work.
Waiting does not consume an agent attempt.
An unchanged PR and unchanged feedback must not trigger repeated paid jobs.

| Child state | Meaning |
| --- | --- |
| `blocked` | Dependencies or another child PR prevent starting |
| `ready` | Eligible for implementation |
| `implementing` | A coding job owns the worktree |
| `pr_open` | A tracked PR exists and needs checks, review, or repair |
| `waiting` | A known external event is outstanding |
| `needs_human` | The runner cannot safely decide or proceed |
| `complete` | Merge into the correct base and issue closure are verified |

Review and job states are separate records rather than multiplying child states.
Every waiting or blocked state includes a reason code, a readable explanation, and the relevant issue, PR, job, or check link.

## 8. Git and worktree lifecycle

Never check out, stash, reset, or clean the developer's ordinary checkout.
A dirty developer checkout is acceptable because work happens elsewhere.
Do not require its local main or feature branch to be checked out or up to date.

Before starting a child, fetch the configured remote and resolve the latest remote feature-branch commit.
Create a deterministic work branch such as `runner/p-100/i-101` and a dedicated worktree from that exact commit.
This is the safe equivalent of pulling the latest feature branch without touching the developer's checkout.
Validate the path remains beneath the runner's worktree root.

If the branch, worktree, or remote branch already exists, inspect its recorded ownership and current state before reusing it.
An unrecognized collision is `needs_human`, not permission to overwrite it.
Never attach a branch already checked out in another worktree.

One child has one work branch and one PR.
Record a stable ownership marker in the PR body containing the repository, parent issue, child issue, and runner work identifier.
Use ordinary issue references rather than relying on closing keywords when the base is a non-default branch.
Find existing PRs by recorded identity, ownership marker, and exact head/base before creating another.

The coding agent makes ordinary local commits but does not push, create PRs, merge, or close issues.
The runner verifies the worktree, reruns configured validation, pushes normally, and creates the PR.
Do not amend published commits, rewrite history, or force-push.
Do not add automatic agent co-author trailers.

Before review or merge, fetch again and require the current base commit to be an ancestor of the PR head.
If needed, merge the latest base into the work branch, rerun validation, push, and obtain a new review.
Simple child-branch conflicts may be repaired by the coding agent within the same bounded repair budget.
Any unexpected remote movement must be reconciled before continuing; a rejected push is never retried with force.

Retain the worktree and branch after merge or failure.
Automatic deletion and broad cleanup are out of scope.
No command may recursively remove an arbitrary path or delete another Copilot session's artifacts.

## 9. Coding-agent contract

Each implementation or repair job starts a fresh noninteractive Copilot session in the assigned worktree.
Pass the complete issue, its linked spec and relevant decisions, repository instructions, parent/base identity, acceptance criteria, validation commands, and any current review or CI findings.
Retrieve linked GitHub material explicitly instead of assuming the agent can see a previous conversation.
If a required source is inaccessible, stop that issue rather than inventing requirements.
Record the issue and spec revision used by each job.
Material requirement changes during implementation invalidate readiness and require an explicit updated job or human decision before merge.

Build a scope manifest from the exact requirement sources supplied to the job.
The manifest includes the repository and issue identity, current issue title and body, parent and child membership, acceptance criteria, linked spec Git blob IDs or content hashes, and explicitly designated decision records or requirement comments.
Ordinary discussion, runner bookkeeping, and review feedback are not requirement sources unless the developer explicitly designates them as such.
Serialize the manifest as canonical UTF-8 JSON and hash it with SHA-256 to produce the scope fingerprint.
Store the manifest with the job and review evidence so a changed fingerprint can be explained rather than appearing as an opaque invalidation.

The implementation prompt follows `/implement`: build in red-green-refactor slices, perform the Standards + Spec self-review, and produce ordinary commits.
Load the installed skill instructions explicitly when available.
Do not assume that placing a slash-command name in a headless prompt invokes that skill.
Missing required workflow skills are a setup error, not a silent fallback to a different process.

The self-review does not substitute for the independent external auto-reviewer.
Do not run `/triage` or repeat grilling for tickets that are already agent-ready.
The trusted runner job explicitly authorizes completing that ticket's subtasks without pausing for approval between each one.
It does not authorize unrelated work or override other repository constraints.

A job writes a small structured result containing its outcome, summary, commit IDs, validation summary, and blockers.
Define and validate the exact schema for this result, including required fields, allowed outcome values, field types, nullability, and rejection of unsupported fields.
The schema does not need its own version number in version one.
The runner independently checks commits and command exit statuses.
Malformed output, a missing result, an unexpected branch change, or a success report with no justified deliverable is a failure.
A no-code result requires human acknowledgement before closing the issue.

Use documented Copilot prompt mode, a known session ID, disabled interactive questioning, and a finite continuation allowance.
The model defaults to the user's configured model.
Do not hardcode a model version or spawn an unbounded agent fleet.
Apply a one-hour wall-clock timeout by default and retain logs on timeout.

Configure permissions for the expected local editing and validation tools instead of blindly granting all tools and paths.
Do not expose runner-owned GitHub mutations as agent tools.
Treat issue bodies, PR comments, and repository text as task data rather than authority to change runner policy.
This remains a trusted local workflow: a worktree and prompt restrictions do not sandbox arbitrary code or malicious shell execution.
Only run it against repositories and contributors the developer trusts.

## 10. Existing PRs, checks, and repair

Discover existing child PRs before launching implementation.
Association must be explicit through issue linkage, an ownership marker, or a developer-supplied mapping.
Do not infer ownership merely from a similar title.

For an existing PR without runner ownership, show `needs_human` and offer the planned `adopt` command.
Adoption verifies the child, exact base branch, same-repository head, and absence of conflicting local work.
It authorizes normal management and repair commits but does not by itself authorize merging.
Merge permission for an adopted PR requires a separate `authorize-merge` operation, and that permission is effective only when the configured autonomy level allows the relevant child or parent merge.
Multiple plausible PRs for the same child require an explicit choice.
A manually closed, unmerged PR does not automatically start over on the next poll.

Before scheduling review, run local validation and wait for known CI to settle.
Use the configured named checks plus all requirements GitHub applies to the target branch.
Read both Checks and commit status contexts, paginate results, and associate them with the current head or the corresponding test-merge revision.
A missing required check is waiting, not green.
A failed, cancelled, or timed-out required check blocks merge.
Neutral or skipped conclusions only satisfy a requirement when GitHub's applicable rules allow them.

A repository without CI can still use the runner if local validation is configured.
Report explicitly that no remote CI gate exists.
Do not manufacture a green CI status from local tests.

Code-related failures and actionable review findings enter a repair job with logs and feedback.
Infrastructure failures, unavailable credentials, unclear requirements, and failing checks unrelated to the change require a visible blocker rather than speculative code edits.
Do not automatically rerun workflows or change workflow permissions to obtain green checks.

Every new commit invalidates prior review approval.
Changes requested by a human may trigger a repair, but the runner never dismisses that person's review or resolves their threads.
Post an evidence-based reply and wait for the human to confirm resolution.
Never repeatedly submit the same reply on unchanged feedback.

Earlier threads authored by the configured auto-reviewer may be resolved after a later full approval covers the repaired head and the runner has recorded the fix or explanation.
New optional findings do not become blocking findings by interpretation.
GitHub's conversation-resolution requirements still apply, and any remaining human threads block unattended merge.

## 11. External auto-reviewer integration

### Invocation

Invoke the script in `PR_AUTO_REVIEWER_PATH` with the selected Python executable and an argument array.
Pass `--repo`, `--gh-host github.com`, `--only-pr`, `--force`, and `--no-learn`.
Use the script's directory as its working directory.
Do not interpolate issue text, repository names, or paths into a shell command.

The verified existing script supports these switches.
`--force` ensures that a base-only change or a new managed review attempt cannot silently reuse a HEAD-only cache.
`--no-learn` keeps this invocation focused on the selected review instead of also updating learned guidance.
The runner's own receipt cache prevents calling it again for unchanged approved input.

`--only-pr` does not guarantee the PR will be reviewed.
The current script still filters by its configured review authors.
Verify that the PR author is eligible and report a skipped target as a blocker.
A zero exit code alone is never an approval.

Use a dedicated reviewer installation for the runner, or disable any independent scheduled invocation against that same installation while the runner is active.
The current reviewer has shared on-disk state and in-process locks, not a cross-process coordination contract.
Version one does not attempt to coordinate two unrelated programs writing that state.

### Evidence and normalized receipt

Use the existing structured review state and review artifacts rather than parsing human-readable console output.
The current script records fields including `head_sha`, `decision`, `posted_event`, review IDs, human-review flags, and reconsideration history.
Its artifact files include the full reviewed head SHA, PR URL, review result, and whether the diff was truncated.
Artifacts and state are repository-scoped according to the reviewer's configured default repository.
The adapter must resolve the correct location; it must not read an unrelated top-level state entry with the same PR number.

Before invocation, record the target head, base, external-feedback fingerprint, and existing reviewer evidence.
After invocation, require evidence of a new completed review or reconsideration from that invocation.
Use a new review ID or a newly appended, timestamped reconsideration record plus its matching artifact.
File existence or modification time alone is insufficient.
A skipped run, ambiguous evidence, reused review ID without a new matching record, incomplete diff, or inconsistent state is not approval.

Record the following normalized receipt in SQLite and retain a copy of the supporting artifacts.

| Field | Required meaning |
| --- | --- |
| `repository`, `pr_number` | Exact GitHub identity |
| `head_sha`, `base_sha` | Full commits observed for this review |
| `job_id`, `review_id` | Invocation identity and verifiable posted review |
| `decision` | `approve`, `request_changes`, `comment`, or `error` |
| `posted_event` | The actual GitHub event, not the model's intended event |
| `needs_human_review`, `manual_approval_pending` | Explicit reviewer policy gates |
| `feedback_fingerprint` | Canonical snapshot of the human feedback covered by the review |
| `scope_fingerprint` | Issue/spec revision and, for integration, selected child membership |
| `reviewer_fingerprint` | Script and relevant configuration identity |
| `completed_at`, `evidence_paths` | Freshness and audit evidence |

Fetch the posted GitHub review to verify its author, commit, state, and continued existence.
Do not trust a bare review ID copied from an older failed attempt.
Reject dry-run evidence, including a dry-run reconsideration of an earlier live review.
Reject a receipt if the head, base, or external human feedback changed during the review.
GitHub does not provide one monotonic cursor across review comments, issue comments, reviews, and thread-resolution changes.
Build the feedback fingerprint from canonical records containing each relevant item's stable identity, author, creation and update timestamps, state or resolution, and body hash.
Sort those records by type and stable identity before hashing them.
Exclude the runner's own bookkeeping and the reviewer's newly posted output from the external-feedback fingerprint to avoid self-triggering loops.
Approval-only votes and reactions are separate merge gates, not new actionable feedback that demands another automated review.

Only `decision = approve` is a positive external-review signal.
Neither `comment` nor the absence of blocking findings counts as approval.
Honor `needs_human_review` and `manual_approval_pending` even when GitHub itself does not demand a human review.
If those flags require human approval, record and verify a subsequent appropriate GitHub approval before merging.

GitHub does not allow a user to formally approve their own PR.
The current reviewer therefore posts its verdict as a comment on self-authored PRs.
A verified structured `approve` verdict posted as a comment can satisfy the runner's external-review gate when self-authorship is the only downgrade reason.
It cannot satisfy a GitHub rule requiring an independent approving reviewer.
Other comment-only policy downgrades require human approval and must not be silently treated as formal approval.

The reviewer adapter is the first implementation risk to retire.
Version one must prove these receipt rules against the installed reviewer before enabling automatic merges.
If its current artifacts cannot identify an invocation unambiguously, add a small versioned result-file contract to the reviewer rather than scraping console prose or weakening the gate.
No change to the external reviewer is performed as part of writing this specification.

## 12. Merge and issue-closure rules

Immediately before every merge, refresh all relevant GitHub facts.
The configured autonomy level must permit that class of merge.
An adopted PR must also have an explicit, recorded merge authorization for that exact PR.
The PR must be open, not draft, runner-owned or explicitly adopted, and target the configured branch.
The recorded head and base must still match the valid review receipt.
Configured local validation must have passed for that head, and the latest base must already be incorporated.
All required checks, applicable GitHub review rules, reviewer policy gates, and conversation requirements must be satisfied.
There must be no unresolved human request for changes, unresolved human thread, or merge conflict.

Use a merge operation that checks the expected head SHA.
Do not use an administrative bypass or ask an agent whether bypassing a rule seems reasonable.
If GitHub rejects the merge, reconcile again and show the actual blocker.
Unknown mergeability or unreadable required-rule information is not permission to proceed.

An expected-head check does not atomically freeze the base branch.
On an unprotected branch, another writer can advance the base between the final read and merge.
Recheck immediately and avoid concurrent runner writes, but document this residual race rather than claiming an impossible guarantee.
Strict up-to-date branch protection is recommended when that race is unacceptable.
Configuring repository protections or merge queues is outside version one.

After a child merge, confirm the merged PR and its recorded base through GitHub.
Close the child explicitly as completed and link the merge evidence.
This is necessary because merging into a non-default feature branch may not automatically close its issue.
If closure fails, retry the closure only; never reimplement or remerge the child.
Closing the issue then triggers immediate dependency reconciliation.

## 13. Parent integration and completion

A parent becomes integration-ready when every selected child is either verifiably complete or explicitly excluded from scope, with no outstanding child work or unresolved blocking relationships.
Refresh the child list before making that decision.
Record its membership so a newly added or reopened child invalidates integration readiness.

Open or explicitly adopt one integration PR from the feature branch to main.
Freeze new child launches for that parent while integration is in progress.
Validate the aggregate feature, not just the individual children, and run the same external-review and merge gates.

If main has advanced, use a normal non-rebasing branch update to incorporate it into the feature branch.
This is an authorized integration-maintenance write, not permission to implement child tickets directly on that branch.
Revalidate and re-review after the update.
If branch protection disallows the update, or feature-to-main conflicts require judgment, mark the parent `needs_human`.
Automatic feature-to-main conflict repair is out of scope for version one.

For integration defects, create or request an explicit follow-up child issue and return the parent to child-work mode.
Do not quietly fix unrelated defects directly on the feature branch.
The existing integration PR can remain open, but it cannot merge until the new child work is complete and the aggregate review is renewed.

Merge the integration PR using a merge commit.
If repository policy requires a different method, stop and report the incompatibility instead of silently changing the policy.
Close the parent only after confirming that merge and rechecking that its selected child set is complete.
If an external actor adds a child during the final merge/closure window, leave the parent open and surface the discrepancy.
If feature work was already integrated externally, verify the integration PR or relevant ancestry rather than creating an empty PR.
An empty parent or a manually closed parent is not by itself evidence of successful integration.

## 14. Local persistence, recovery, and limits

Use an operating-system-held exclusive file lock keyed to the repository's canonical Git common directory.
The lock prevents two runner invocations with different configuration files from operating on the same local repository.
A read-only `status` command does not need the mutation lock.
Operator commands can submit transactional SQLite control requests while the runner holds the lock.
The lock-holding executor applies those requests before its next external action, so `pause` can work without authorizing a second executor.
Separate clones on different machines are not coordinated.

Use SQLite transactions for jobs, issue ownership, action intents, attempts, receipts, and pauses.
Do not hold a database transaction open during a subprocess or GitHub request.
Before an external mutation, record its intended action and stable identity.
Each mutation intent has a deterministic idempotency key derived from the repository, action kind, target identity, and expected input state.
Enforce uniqueness for active and successfully completed intent keys so a restart cannot dispatch the same logical mutation twice.
An intent must reach an observed terminal outcome or an explicit ambiguous state before another intent for that logical operation can be created.
Afterward, record the observed result.

On restart, reconcile unfinished actions with GitHub and Git before retrying.
For example, a push may have succeeded even if its response was lost, and a PR may have been created before the database was updated.
Use exact branch names and ownership markers to recover those outcomes.
The goal is repeatable reconciliation, not an exactly-once claim about remote requests.

Store live-qualification receipts in SQLite.
A receipt records the disposable repository and exercise run, observed child and parent transitions, relevant tool and reviewer versions, runner commit or package identity, completion time, and retained evidence paths.
The `merge_children` level requires a successful exercise through child merge and restart recovery.
The `merge_parents` level requires the full two-child dependency, review invalidation, restart, and parent-integration exercise.
Changing to an unsupported major tool version or materially incompatible reviewer version invalidates the receipt and caps autonomy at `create_pr` until qualification succeeds again.
The normal `doctor` compatibility rules may still block all mutating work when a required interface cannot be trusted.

Supervise child processes and record their PID, start time, and session or job identity.
Do not assume a leftover PID still identifies the same process.
If a previous job might still be alive and cannot be safely identified, pause mutation and request intervention.
Never launch a replacement agent into a worktree that may still be in use.
On Windows, place every owned subprocess and its descendants in a Job Object configured for kill-on-close behavior.
Use the recorded process creation time and job identity when reconciling an interrupted run rather than trusting a reused PID.
Terminate only the owned process tree when cancelling or timing out.

| Limit | Default behavior |
| --- | --- |
| Coding timeout | Stop the job after 60 minutes |
| Review timeout | Stop the job after 30 minutes |
| Repair budget | Three repair jobs per child; integration defects require an explicit follow-up child |
| Infrastructure retries | Two retries with one-minute and five-minute delays |
| Per-run job budget | Stop dispatching after 20 coding/review jobs |
| Repeated no-progress result | Escalate to `needs_human` |
| Rate limiting | Honor GitHub's retry or reset time without spinning |

Persist attempt counters across restarts and head changes.
Do not reset the repair budget merely because another commit was pushed.
Only an explicit retry/reset operation can clear an exhausted budget.
Record that operation and its reason.
The per-run job allowance intentionally resets when the developer starts a new run, while lifetime issue/PR limits remain.
These limits bound work but do not promise a hard monetary cost cap.

Unexpected errors must include actionable diagnostics and retained logs.
Global authentication or configuration failures stop the run.
A blocked issue does not prevent independent parents from progressing.
Do not silently default to an empty issue list, empty review list, or successful check result after a failed read.

## 15. CLI and operator experience

These are proposed commands to implement, not commands that exist today.

| Command | Behavior |
| --- | --- |
| `doctor` | Validate local setup and remote read access without paid jobs or GitHub mutations |
| `plan` | Read current facts and print the next transition without Git mutations or job dispatch |
| `run` | Reconcile until interrupted, globally blocked, or its job budget is reached |
| `run --once` | Complete at most one selected transition, then exit |
| `status` | Show parents, children, PRs, last jobs, budgets, and precise blockers |
| `pause` / `resume` | Persistently pause or resume all work or a selected parent/child |
| `adopt` | Explicitly authorize managing and repairing an existing matching PR without granting merge permission |
| `authorize-merge` | Explicitly grant merge permission for one adopted PR, subject to the configured autonomy level and all normal gates |
| `acknowledge` | Record a reason for treating an already completed no-code issue as satisfied |
| `retry` | Clear an explicit blocker or exhausted budget after operator review |
| `qualify` | Run the separately authorized live compatibility exercise and record its evidence for autonomy promotion |

Every command accepts an explicit configuration path.
`run --once` may wait for one selected coding or review job to finish; it is not a hidden continuous run.
`plan` may perform remote reads but does not invoke the reviewer, run validation commands, fetch into the working repository, or change runner action state.
Do not apply a dry-run flag only to merges while allowing other mutations.

Show one concise line per meaningful transition, including the parent, issue or PR, action, result, and log path.
Do not dump full prompts or large diffs into the console by default.
Keep detailed stdout and stderr in job-specific log files.
Print a heartbeat while waiting so the process does not look hung.

The first Ctrl+C stops dispatch and requests graceful cancellation of an active job.
A second Ctrl+C terminates the owned subprocess tree.
Preserve all work and record the interrupted action for reconciliation on restart.
Pausing through another CLI invocation stops new transitions but does not silently kill an active job.
Recheck pauses before every push, PR creation, merge, and issue closure.

Running in a terminal is the default.
Task Scheduler can later invoke `run --once`, but installing a scheduled task or Windows service is not part of version one.
The machine must remain awake, and the tool resumes from fresh facts after sleep.

## 16. Implementation shape

Keep this as a small new Python package rather than extending the existing Actions-oriented PR Flywheel.
The existing flywheel has different deployment assumptions, owns its own review process, and does not manage this issue-to-worktree lifecycle.
Reuse useful conventions and tests where behavior matches, but do not inherit unrelated policies or create a framework for hypothetical backends.

The main testable interface is `reconcile_once`, which accepts its adapters and returns the observed transition.
Inside it, a pure `decide_next(snapshot, local_state)` function produces a typed next action.
An execution module performs that action, and the reconciliation module persists its observed result.
The CLI loop repeatedly calls this same interface rather than duplicating scheduling logic.
This keeps scheduling and merge eligibility testable without an LLM, network, or real clock.
Define the next-action and observed-transition records as exact validated tagged unions with documented fields and allowed values.
These schemas do not need independent version numbers in version one.

Keep narrow internal seams for GitHub/Git execution, bounded subprocess execution, and reviewer evidence normalization.
Use real adapters in normal runs and deterministic fakes in tests.
Use typed dataclasses or equivalent validated records instead of passing nested untyped dictionaries throughout the program.
Use standard-library TOML and SQLite support.
Do not add a general plugin system, arbitrary workflow DSL, event bus, or backend registry.

The existing `orchestrator` integration tests provide useful precedent for lifecycle and bounded-loop scenarios.
This runner needs additional tests through its own public reconciliation interface and real temporary Git repositories.
Tests must assert observed actions and preserved state, not private helper call counts.

## 17. Acceptance and testing

Implement each behavior test-first through the highest useful interface.
Most coverage should use a fake GitHub adapter, fake agent/reviewer executables, a controllable clock, SQLite, and actual temporary local Git repositories.
No ordinary automated test should require paid model calls or mutate a real repository on GitHub.

| Scenario | Required outcome |
| --- | --- |
| Fresh child | Worktree starts at the latest remote feature SHA, not stale local main |
| Dependency chain | The next child starts only after its prerequisite is merged and available in its base |
| Cross-parent dependency | A child merged only into another feature branch does not unblock dependent work |
| Repeated idle polls | No duplicate agent call, review, comment, branch, or PR |
| Base or head changes | Approval is invalidated and validation/review run against updated code |
| Reviewer skips target | Zero process exit does not become approval |
| Reviewer returns comment | The PR remains unapproved |
| Self-authored PR | Verified structured approval can satisfy the local gate, but not independent-review rules |
| Reviewer policy gate | Manual-review requirements cannot be bypassed with a comment-only verdict |
| `create_pr` autonomy | PR creation, repair, and review may proceed, but child and parent merge actions cannot be selected or executed |
| `merge_children` autonomy | Eligible child PRs may merge, but integration PRs cannot merge |
| Adopted PR without merge authorization | Management and repair may proceed, but merge remains blocked |
| Missing or stale qualification receipt | Autonomy promotion is refused and prohibited merge transitions remain unavailable |
| Reviewer stale or dry-run artifacts | The merge is blocked |
| New human feedback during review | The receipt is rejected as stale |
| Required check missing or failed | No merge |
| Human request for changes | Repairs may occur, but the runner does not dismiss the review |
| Crash after PR creation | Restart adopts the exact recorded operation rather than creating a duplicate |
| Crash after merge | Restart closes the issue without another implementation or merge |
| Dirty developer checkout | Existing files remain unchanged |
| Conflicting worktree or branch | Stop that issue without overwriting or deleting anything |
| Second runner | Mutation lock is refused clearly |
| Agent timeout or ambiguous orphan | Preserve work and do not launch a competing agent |
| Budget exhausted | Pause expensive jobs with an explicit reason |
| Parent children finish | Create and review an integration PR; do not close the parent early |
| New or reopened child | Parent readiness and its integration approval are invalidated |
| Parent merge confirmed | Close the parent and retain all merge evidence |
| Read-only plan | No validation command, paid job, local Git write, or GitHub mutation |
| Windows paths with spaces | Interpreter startup and argument passing work without shell interpolation |

Before calling the tool usable, run one explicit end-to-end exercise in a developer-authorized disposable GitHub repository.
Use a parent with two dependent children, real coding-agent invocations, and the installed external reviewer.
Exercise a requested change, a new commit invalidating approval, a restart after a merge, and final feature integration.
Confirm actual files, commits, PR bases, review evidence, issue closures, and preserved worktrees.
The developer authorizes this paid and mutating exercise separately from ordinary automated tests.
The `qualify` command records the resulting compatibility receipt and retained evidence.
Automatic child or parent merging remains unavailable until the corresponding portion of this exercise has succeeded for the installed toolchain.
Qualification uses a dedicated, explicitly authorized execution path scoped to the configured disposable repository.
It does not temporarily raise the autonomy of ordinary reconciliation against the developer's real repository.

## 18. Suggested implementation slices

1. Prove the reviewer contract with recorded fixtures and one authorized live PR, including self-authored and skipped-target cases.
2. Build configuration, version and interface compatibility checks, `doctor`, and read-only GitHub snapshots.
3. Add deterministic selection, scope manifests, fair cross-parent scheduling, and read-only `plan`.
4. Complete one child through an isolated worktree, bounded coding job, validated structured result, and local validation.
5. Add push and correctly targeted PR creation, then prove the complete `create_pr` autonomy level end to end.
6. Add normalized review receipts and stale-evidence invalidation without enabling merges.
7. Add CI and review-driven repair, attempt budgets, and precise waiting or human-intervention blockers.
8. Add runner ownership, separate adoption and merge authorization, child merge gates, and explicit child issue closure.
9. Add SQLite action intents, restart reconciliation, repository locking, pauses, cancellation, and bounded continuous polling.
10. Add dependency progression and prove fair scheduling across parents with existing PR work.
11. Add parent integration readiness, aggregate validation and review, integration repair handling, and parent merge gates.
12. Add the disposable-repository qualification exercise, compatibility receipts, and staged autonomy promotion.

Each slice should have a runnable vertical outcome rather than delivering isolated infrastructure first.
Use `/to-tickets` to split these slices further where needed into self-contained tickets with explicit blocking edges, then use a fresh `/implement` session per ticket.
Preserve this document as the shared spec and keep the external auto-review requirement distinct from each implementation session's own code review.

## 19. Deliberate exclusions and remaining limits

This version does not plan features, write specs, triage tickets, or interview the developer.
It does not support parallel coding agents, multiple machines, fork PRs, cross-repository dependency execution, or nested issue hierarchies.
It does not install branch protections, manage deployment credentials, deploy applications, or automatically merge feature-to-main conflicts.
It does not create or delete Git worktrees outside its owned worktree root.
It does not automatically clean completed worktrees, branches, logs, or unrelated Copilot sessions.
It does not promise to defeat malicious repository code, provide exact dollar budgets, or guarantee atomicity across GitHub and SQLite.

The important pre-build compatibility checks are the installed reviewer's evidence contract, the repository's feature-branch and main merge policies, and noninteractive Copilot permissions.
All have explicit stop conditions.
There is no requirement to solve distributed scheduling or production operations before this becomes useful locally.

## 20. Design references

- [Ask Matt flow](../../.agents/skills/ask-matt/SKILL.md) for the spec-to-tickets-to-implementation workflow.
- [Phase boundaries](../../.agents/skills/ask-matt/PHASE-BOUNDARIES.md) for fresh implementation contexts and retained primary sources.
- [Existing orchestrator internals](../../orchestrator/README.md) for the current, separate PR Flywheel design.
- [Existing lifecycle tests](../../orchestrator/tests/test_integration.py) for bounded-loop testing precedent.

The external reviewer behavior described here was inspected in `C:\git\fde-pr-auto-reviewer\auto_review.py` on 2026-09-18.
Relevant entry points include `main`, `_apply_runtime_context`, `_record_initial_review`, `_record_reconsider`, `_effective_event`, and the review-artifact writers.
The adapter must validate compatibility when the reviewer changes instead of assuming these implementation details are a permanent public contract.
