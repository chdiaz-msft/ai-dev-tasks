# Local Issue Runner

**Status:** Proposed specification, ready to split into implementation tickets.
**Date:** 2026-09-25.
**Scope:** A Windows-first Python program for one developer, one local machine, and one GitHub repository per invocation.

## 1. Problem and intended outcome

I already have a spec and agent-ready GitHub tickets created through Matt Pocock's framework.
Several parent issues group those tickets into features, and each feature has a branch that will eventually merge into main.
I want to leave a local program running that implements independent eligible tickets in parallel, publishes PRs, responds to their comments and blocking CI, and closes issues after verified merges.
Each feature has its own independent directed acyclic graph (DAG) of tasks.
The runner may also merge its PRs when automatic merging is explicitly enabled and all gates pass.
Running reviewers is outside the product's scope.

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
| Task model | One independent DAG per feature; `A -> B` means B depends on A |
| Concurrency | Independent issues may run together, with at most one coding job per delivery |
| Work in progress | Five active child issues per feature by default, including waiting PRs |
| GitHub access | Use authenticated `gh` commands and structured JSON |
| Persistence | One SQLite database per repository, with task/edge tables and separate worktrees, logs, and evidence files |
| Graph operations | Rebuild Python's standard-library `graphlib.TopologicalSorter` from durable records |
| Configuration | One TOML file; no mandatory GitHub labels or Git tags |
| PR ownership | Publish and maintain only PRs created through recorded runner actions; no adoption |
| Autonomy | Automatic merging is off by default; child and parent merging require explicit promotion |
| Branch updates | Merge the latest base into an existing PR branch; never rebase published branches |
| Child merge strategy | Squash into the parent's feature branch |
| Parent merge strategy | Merge commit into main, retaining feature-branch ancestry |
| Review | Do not invoke reviewers; enforce the approvals required by GitHub |
| Feedback | Reply to every external PR comment; fix worthwhile feedback or explain and resolve it |
| Merge delay | One quiet hour after publication, a push, or new external feedback, configurable |
| Finalization | Close a parent only after its feature has actually merged into main |
| Cleanup | Remove safely completed runner-owned worktrees and child branches; retain feature branches and evidence |

There is no database server, graph database, message broker, web dashboard, webhook listener, or distributed lock.
There is no requirement to use an LLM for an idle polling cycle.
Parallel work is bounded by each feature's active-issue limit, not an unbounded agent fleet.
There are no lifetime, per-run, or repair job-count caps.
Timeouts and a verified no-progress rule still prevent stuck jobs from running indefinitely.

Autonomy is deliberately staged:

1. `create_pr` is the default and lowest level.
   The runner may implement, validate, push, publish child and integration PRs, and maintain their comments and CI, but it must never merge a PR.
   After a human merges a correctly targeted PR, the runner verifies the result, closes the corresponding issue, and advances eligible work.
2. `merge_children` adds automatic child-PR merging after all gates pass.
3. `merge_parents` also permits automatic integration-PR merging after all gates pass.

Moving to a higher level requires an explicit configuration change and a valid live-qualification receipt for the installed toolchain.
A lower autonomy level must make prohibited transitions unavailable in the decision and execution layers rather than relying only on a prompt or operator convention.
Qualification covers the runner's merge and recovery behavior, not an external reviewer's contract.

## 3. User stories

1. As the developer, I can configure parent issue numbers, their feature branches, and the main branch.
2. As the developer, I can check configuration and authentication without starting paid jobs.
3. As the developer, I can preview the next action without changing GitHub or my repository.
4. As the developer, I can leave the runner open and have it discover remaining child issues.
5. As the developer, I can inspect each feature's DAG and run independent ready issues in parallel.
6. As the developer, I get a separate branch and worktree for each child delivery, reused for its repairs.
7. As the developer, I know the runner will not modify PRs published outside the tool.
8. As the developer, I get responses to PR comments and repairs for worthwhile feedback and blocking CI.
9. As the developer, I can enable merging without bypassing GitHub's approval or branch-protection rules.
10. As the developer, I can see why a PR is waiting instead of having the runner retry indefinitely.
11. As the developer, I can stop and restart the process without creating duplicate PRs.
12. As the developer, I get a final integration PR before a parent is marked complete.
13. As the developer, I retain logs, commits, and worktrees when a job fails.
14. As the developer, I can pause an issue or the whole runner without deleting its work.
15. As the developer, I can reopen delivered issues and get new tracked deliveries rather than reusing historical completion.
16. As the developer, I get automatic cleanup of safely merged work without losing logs or audit records.

## 4. Domain model and sources of truth

A **parent** is a configured feature-tracking GitHub issue.
A **child** is an implementation issue belonging to exactly one configured parent.
A **feature DAG** contains that parent's selected child tasks and a final integration step.
An edge **`A -> B`** means B depends on A, so A must be verifiably complete and available in B's base before B starts.
A **delivery** is a numbered attempt to deliver an issue's current scope through a PR.
Reopening a delivered issue starts a new delivery number; an old merged PR does not complete the new delivery.
A **feature branch** is the integration branch for a parent's children.
A **work branch** and **worktree** belong to one child delivery and are reused across its implementation and repair jobs.
An **integration PR** is the feature-branch-to-main PR for one parent delivery.
An **active slot** belongs to an issue delivery from the start of implementation until its PR merges, is abandoned, or is explicitly released.
An open PR waiting on CI, comments, or a person still occupies its slot.
A **job** is one bounded invocation of a coding agent for implementation, repair, or feedback handling.
A **validation record** binds command results to an exact committed tree and the effective validation configuration.
A **qualification receipt** records a successful, explicitly authorized live merge-safety exercise.
A **blocker** is a reason that prevents progress, with an explicit distinction between waiting on an external event and requiring human intervention.

GitHub is authoritative for issue state, dependency relationships, PR state, branch tips, checks, reviews, and merges.
Git is authoritative for local changes and commit ancestry.
SQLite records graph snapshots, delivery history, PR and worktree ownership, jobs, feedback handling, validation, qualification, pauses, and recovery information.
Stored snapshots do not override newer GitHub facts.
An agent's success message is never proof that tests passed, a PR exists, or a merge happened.
An in-memory graph is a calculation over those facts, never the source of durable execution state.

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
max_active_issues_per_feature = 5
ci_wait_timeout_seconds = 3600
merge_quiet_seconds = 3600
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
# Replace native discovery; tracked integration-repair children may be added.
child_issues = [201, 202, 203]
```

The active-issue limit applies separately to every feature, not to the repository as a whole.
With two configured features and the default limit, up to ten independent child deliveries may be active.
Each delivery may have at most one coding job at a time.
There is no reviewer executable, reviewer environment variable, or reviewer configuration to supply.

Store runtime files under `%LOCALAPPDATA%\LocalIssueRunner\<repository-key>`.
The repository key includes the canonical local Git common directory and GitHub repository identity.
Keep the database, logs, job input files, validation and merge evidence, and worktrees outside the developer's ordinary checkout.
Do not commit credentials or generated runtime state.
Use a local filesystem, not a network share or synchronized cloud folder, for live SQLite state and worktrees.
No new GitHub labels or Git tags are required for discovery, scheduling, or ownership.

Version one supports GitHub.com, existing local clones, existing remote feature branches, and same-repository PRs.
It does not create feature branches automatically or infer a repository from a PR number alone.
The configured remote must resolve to the configured repository.
Different parents must have different feature branches, none of which may be main.

`doctor` must check the repository identity, refs, branch names, tool availability, `gh` authentication, Copilot authentication, and GitHub permissions.
It must verify supported `gh`, Copilot CLI, Python, and Git versions and exercise the machine-readable interfaces on which the runner relies.
An unknown major version or an unavailable required structured-output interface blocks mutating commands until compatibility is confirmed.
It must also verify that each configured script can start with its intended interpreter and that command arguments are passed correctly on Windows.
Use help, syntax checks, or other non-mutating startup checks, not a purported dry run that can still invoke an LLM.
At least one repository-appropriate validation command is required before enabling unattended implementation.
Report whether each target branch has readable, effective strict up-to-date protection, required checks, and compatible merge policies.
PR publication remains useful without those protections, but automatic merging is unavailable where its gates cannot be verified.

Copilot CLI is the initial coding-agent backend and uses the developer's existing authenticated installation.
A separate model endpoint or API key is not required for this backend.
Any later backend that requires an endpoint and credentials must obtain and smoke-test them before implementation runs are enabled.
Do not print credentials or put them in job prompts.

## 6. Discovering work and scheduling feature DAGs

By default, retrieve the parent's native GitHub sub-issues and paginate all results.
When `child_issues` is present, it replaces native discovery as the configured child list for that parent.
Do not combine the two discovery modes or use an LLM to guess issue numbers from prose.
The only automatic additions are explicitly tracked integration-repair children created under section 13.
Include those additions in the effective selected scope in either mode without rewriting the TOML file.
Display the discovery mode, effective child list, and origin of every added repair child in `status`.

Support one parent-to-child level in version one.
Reject nested parents, duplicated children across configured parents, and missing issues with a specific explanation.
An empty child list is a configuration problem, not evidence that a feature is complete.

Read native GitHub blocking relationships for each child.
Do not infer a dependency from an ordinary issue mention.
If dependency data cannot be read completely, that child is not eligible.
Each feature has its own independent DAG; do not construct one combined task graph or a cross-feature coordination graph.
Cross-feature and cross-repository dependencies are unsupported, even if the prerequisite has already reached main.
Report the offending edge and block affected work until the developer removes it or reorganizes the feature.
Dependencies on issues outside the effective selected feature scope are likewise not silently treated as satisfied.

Represent the graph with task records and directed dependency-edge records.
An edge stores the feature identity, prerequisite task identity, and dependent task identity.
Within each feature, add a final integration node that depends on all selected child tasks.
This node is a scheduling step, not another GitHub issue.
Detect cycles before starting affected work, and repeat the check when relationships change.
Show the cycle and block its nodes and their downstream dependents while continuing unrelated ready work.
Graph validity and readiness must not depend on the order in which GitHub pages were fetched.

An implementation dependency is satisfied only when its completion is verified and its code is available in the prospective child's base branch.
For a child prerequisite, this normally means its current delivery's PR has merged into the same feature branch and its issue closure is verified.
Use commit ancestry to verify availability, not issue state alone.
Check the merged PR's resulting merge or squash commit, not the original work-branch head that a squash merge replaces.
A completed coding job or an open PR does not satisfy a dependency.

An issue explicitly closed as not planned is an intentional scope exclusion, not an implemented dependency.
Do not run a dependent child until the developer removes that blocking relationship.
A completed child with no verifiable merged PR needs a human decision rather than being assumed implemented.
For legitimate no-code work, an explicit local acknowledgement with a recorded reason may count it as satisfied.
Acknowledgements never override validation or merge requirements for a PR that does exist.
If a human closes a parent before verified integration, pause its remaining work rather than reopening it or continuing implementation.

Within a parent, start independent ready issues until its active slots are full.
Choose an eligible issue that unblocks the most remaining children, then break ties by issue number.
Separate worktrees provide isolation, but DAG independence does not imply that two issues edit different files.
Across parents, use round-robin scheduling and persist the last-served parent.
Apply the same cross-parent fairness to implementation, repair, and feedback-handling jobs for existing PRs.
Cheap recovery, merge bookkeeping, and issue closure may retain global priority because they finish already-observed work without consuming another paid job.
Actionable runner-owned PRs take priority over starting new work, but waiting PRs do not prevent independent work from using remaining slots.
Waiting PRs still count against their feature's limit and never consume another slot merely because they need repair.
A blocked feature must not prevent another feature from progressing.
If all five default slots are occupied by PRs blocked on a reopened prerequisite, pause that feature with a capacity explanation until the operator releases a slot.
Do not silently exceed the configured limit to escape that blockage.
Releasing ownership does not complete an issue, satisfy an edge, or grant permission to delete its work.

## 7. Reconciliation loop

The central operation is `reconcile_once`, which reads current facts, selects at most one actionable transition, executes it, and records the observed result.
A transition can start or collect a coding job, update a branch, push, create a PR or repair issue, reply to or resolve feedback, merge, close or reopen an issue, clean up owned work, or record a blocker.
Do not perform a long chain of mutations from one stale snapshot.

The long-running command repeatedly invokes this operation.
Starting a job records and supervises it without blocking the entire reconciliation loop until that job exits.
Subsequent transitions may dispatch independent jobs while earlier jobs run.
Use one lock-holding coordinator for durable scheduling and remote mutations, not independent schedulers inside each coding agent.
Local jobs may operate concurrently only in distinct owned worktrees.
Do not push, update, clean up, or start another job in a worktree whose current job is still running.
After a successful transition, reconcile immediately so a completed merge can unblock another issue without waiting five minutes.
Owned job completion can wake the loop without waiting for the next remote poll.
When there is no immediate action, wait for `poll_seconds`.
Do not replay missed ticks after machine sleep or queue overlapping polls.
Continue polling requirements and GitHub facts at that interval while jobs run.
Cancel an affected job as soon as a changed requirement is detected; this is detection on the normal five-minute default poll, not an instantaneous webhook response.

The action priority is recovery of an interrupted transition, completion bookkeeping for confirmed merges, progress on existing PRs, parent integration, and finally new child work.
Schedule safe cleanup after merge bookkeeping without blocking independent implementation.
Waiting does not consume an agent attempt.
An unchanged PR and unchanged feedback must not trigger repeated paid jobs.

| Child state | Meaning |
| --- | --- |
| `blocked` | Dependencies, invalid graph data, or full feature capacity prevent starting |
| `ready` | Eligible for implementation |
| `implementing` | A coding job owns the worktree |
| `pr_open` | A tracked PR exists and needs checks, feedback handling, repair, or merge gating |
| `waiting` | A known external event is outstanding |
| `needs_human` | The runner cannot safely decide or proceed |
| `complete` | Merge into the correct base and issue closure are verified |

Job, feedback, delivery, and cleanup states are separate records rather than multiplying child states.
Every waiting or blocked state includes a reason code, a readable explanation, and the relevant issue, PR, job, or check link.

## 8. Git and worktree lifecycle

Never check out, stash, reset, or clean the developer's ordinary checkout.
A dirty developer checkout is acceptable because work happens elsewhere.
Do not require its local main or feature branch to be checked out or up to date.

Before starting a child, fetch the configured remote and resolve the latest remote feature-branch commit.
Create a deterministic work branch such as `runner/p-100/i-101/d-1` and a dedicated worktree from that exact commit.
This is the safe equivalent of pulling the latest feature branch without touching the developer's checkout.
Validate the path remains beneath the runner's worktree root.
Reuse that worktree for all jobs in the delivery, rather than creating one per poll or per repair.
Parent integration has its own owned worktree; it must not take over a branch checked out in the developer's checkout.

If the branch, worktree, or remote branch already exists, inspect its recorded ownership and current state before reusing it.
An unrecognized collision is `needs_human`, not permission to overwrite it.
Never attach a branch already checked out in another worktree.

One child delivery has one work branch and one PR.
Reopened delivered work gets a fresh delivery number, work branch, worktree, and PR.
Record a stable hidden ownership marker in the PR body containing the repository, parent issue, child issue where applicable, delivery number, and runner work identifier.
Use ordinary issue references rather than relying on closing keywords when the base is a non-default branch.
Find existing PRs by the recorded creation intent, ownership marker, and exact head/base before creating another.
A copied marker or similar title alone does not prove that the tool created a PR.

The coding agent makes ordinary local commits but does not push, create PRs or issues, post comments, resolve threads, merge, or close issues.
The runner verifies the worktree, reruns configured validation, pushes normally, and creates the PR.
Do not amend published commits, rewrite history, or force-push.
Do not add automatic agent co-author trailers.

Before automatic merge, fetch again and require the current base commit to be an ancestor of the PR head.
If needed, merge the latest base into the work branch, rerun validation, and push.
This starts a new quiet period and requires fresh CI and GitHub approval checks.
Simple child-branch conflicts may be repaired by the coding agent, subject to the no-progress rule.
PR repairs may run concurrently; there is no serialized reviewer or whole-feature repair lane.
Serialize actual merge operations and reconcile the updated base before selecting the next one.

Normal external commits on a managed work branch are supported.
After its active job ends, reconcile fast-forward movement or merge non-rewriting divergent commits, preserve both histories, and revalidate before pushing.
Unexpected uncommitted human edits, rewritten history, or ambiguous local ownership require human intervention.
A rejected push is never retried with force.

After verified merge and completion bookkeeping, automatically remove the delivery's clean, unused, runner-owned worktree and its runner-created local and remote child branches.
Also remove the completed parent integration worktree, but retain the feature branch for later deliveries.
Before each deletion, recheck ownership, exact path or ref, current tip, absence of active jobs, and absence of unexpected local or remote work.
Use Git-aware worktree and branch operations, not broad recursive filesystem deletion.
A squash merge does not preserve the work head as an ancestor; verify the PR's merged head, resulting commit, and recorded base instead of treating an ancestry-only branch deletion check as proof of completion.
Guard remote branch deletion against a changed tip so cleanup cannot delete a new human contribution.
Preserve dirty, ambiguous, failed, or still-used worktrees and branches and report the cleanup blocker.
Never delete feature branches, externally created branches, logs, qualification evidence, or SQLite audit records as part of this cleanup.
No command may recursively remove an arbitrary path or delete another Copilot session's artifacts.

## 9. Coding-agent contract

Each implementation or repair job starts a fresh noninteractive Copilot session in the assigned worktree.
Pass the complete issue, its linked spec and relevant decisions, repository instructions, parent/base identity, acceptance criteria, validation commands, and any current review or CI findings.
Retrieve linked GitHub material explicitly instead of assuming the agent can see a previous conversation.
If a required source is inaccessible, stop that issue rather than inventing requirements.
Record the issue and spec revision used by each job.
When polling detects changed requirements, cancel only affected jobs, wait for their owned processes to stop, preserve partial work, and automatically start fresh jobs against the latest scope.
Do not push a cancelled job's outdated result or require separate human acceptance of every requirement edit.
Requirements that are unreadable or too unclear to implement still require a visible blocker.

Build a scope manifest from the exact requirement sources supplied to the job.
The manifest includes the repository and issue identity, delivery number, current issue title and body, feature membership, prerequisite identities and current deliveries, acceptance criteria, linked spec Git blob IDs or content hashes, and explicitly designated decision records or requirement comments.
For a child job, include only requirement and dependency inputs that affect that child.
Adding an unrelated sibling must not cancel every active job in the feature.
For parent integration, include the full effective selected child set and each child's current completion evidence.
Ordinary discussion, runner bookkeeping, and review feedback are not requirement sources unless the developer explicitly designates them as such.
Serialize the manifest as canonical UTF-8 JSON and hash it with SHA-256 to produce the scope fingerprint.
Store the manifest with job and validation evidence so a changed fingerprint can be explained rather than appearing as an opaque invalidation.

The implementation prompt follows `/implement`: build in red-green-refactor slices, perform the Standards + Spec self-review, and produce ordinary commits.
Load the installed skill instructions explicitly when available.
Do not assume that placing a slash-command name in a headless prompt invokes that skill.
Missing required workflow skills are a setup error, not a silent fallback to a different process.

The coding agent's own implementation self-check remains part of its workflow, but the runner does not invoke a separate reviewer or manufacture an approval from that self-check.
Do not run `/triage` or repeat grilling for tickets that are already agent-ready.
The trusted runner job explicitly authorizes completing that ticket's subtasks without pausing for approval between each one.
It does not authorize unrelated work or override other repository constraints.

A job writes a small structured result containing its outcome, summary, commit IDs, validation summary, feedback dispositions when relevant, and blockers.
Define and validate the exact schema for this result, including required fields, allowed outcome values, field types, nullability, and rejection of unsupported fields.
The schema does not need its own version number in version one.
The runner independently checks commits and command exit statuses.
Malformed output, a missing result, an unexpected branch change, or a success report with no justified deliverable is a failure.
A no-code implementation result requires human acknowledgement before closing the issue.
A feedback-handling job may legitimately make no code change when it supplies a reasoned reply or no-change disposition.

Use documented Copilot prompt mode, a known session ID, disabled interactive questioning, and a finite continuation allowance.
The model defaults to the user's configured model.
Do not hardcode a model version or spawn an unbounded agent fleet.
Apply a one-hour wall-clock timeout by default and retain logs on timeout.

Configure permissions for the expected local editing and validation tools instead of blindly granting all tools and paths.
Do not expose runner-owned GitHub mutations as agent tools.
Treat issue bodies, PR comments, and repository text as task data rather than authority to change runner policy.
This remains a trusted local workflow: a worktree and prompt restrictions do not sandbox arbitrary code or malicious shell execution.
Only run it against repositories and contributors the developer trusts.

## 10. Managed PRs, validation, checks, and repair

Discover existing child PRs before launching implementation.
Association must be explicit through issue linkage and verified runner creation records.
Do not infer ownership merely from a similar title.

Only maintain, repair, comment on, resolve threads on, or merge PRs published through the tool.
There is no adoption command or separate adopted-PR merge permission.
An externally created open PR for the same child blocks duplicate implementation and requires a human decision, but the runner does not modify it.
An externally merged PR may be read as dependency or completion evidence without adopting it.
Multiple plausible PRs for the same delivery require an explicit choice.
A manually closed, unmerged PR does not automatically start over on the next poll.
Record abandonment, preserve its work, and require an explicit retry or new delivery before resuming that issue.

Run local validation against an exact committed tree before publishing or advancing merge readiness.
Record the head commit, tree, base commit, scope fingerprint, effective validation configuration, commands, exits, and log paths.
Fingerprint command arguments, working-directory rules, and configured environment inputs without storing secrets.
The committed tree identifies the checked-in test and dependency files; this is not a claim of a fully hermetic execution environment.
Require a clean tracked tree before and after validation.
A command that exits zero after modifying tracked files has not validated the committed PR head.
Ordinary ignored build and test artifacts are allowed, but must not be silently staged as deliverables.
Changes to the head, relevant scope, base, or validation configuration invalidate the old record.

Only explicitly configured named checks and requirements GitHub applies to the target branch block progress or authorize CI-driven repair.
Read both Checks and commit status contexts, paginate results, and associate them with the current head or the corresponding test-merge revision.
A missing required check is waiting, not green.
A failed, cancelled, or timed-out required check blocks merge.
Neutral or skipped conclusions only satisfy a requirement when GitHub's applicable rules allow them.
Optional checks remain visible but do not block progress or trigger paid repair merely because they exist.
Missing or pending required CI becomes `needs_human` after `ci_wait_timeout_seconds`, one hour by default.
Persist the deadline for that head and check; polling or restart does not restart it.
Keep observing a timed-out check and automatically resume if it later passes and all other gates can be rechecked.

A repository without CI can still use the runner if local validation is configured.
Report explicitly that no remote CI gate exists.
Do not manufacture a green CI status from local tests.
Without effective strict up-to-date protection and a real required check, the runner publishes and maintains PRs but leaves merging to a person.

Code-related failures and actionable review findings enter a repair job with logs and feedback.
Infrastructure failures, unavailable credentials, unclear requirements, and failing checks unrelated to the change require a visible blocker rather than speculative code edits.
Do not automatically rerun workflows or change workflow permissions to obtain green checks.

Recheck GitHub's current approval requirements after every head or base change.
Do not cache an approval count as permanent authorization.
The runner does not add an independent approval requirement beyond GitHub's applicable rules.

## 11. PR comments and feedback handling

The runner does not invoke reviewers, inspect reviewer-local artifacts, or require a private reviewer receipt.
Reviews may be supplied by humans or existing external automation independently of this product.
Read their posted comments and GitHub review states through normal GitHub interfaces.

While a tool-published PR is open and actively managed, respond to every new or materially edited external comment, including human and bot comments, review-thread comments, issue-style PR comments, and nonempty review bodies.
Keep the response proportional; informational comments may need only a brief acknowledgement.
Exclude only the runner's own recorded replies and bookkeeping, not every comment by the authenticated user.
A human using the same account as the runner is still an external feedback source.
Do not reply repeatedly to unchanged input or answer the runner's own output.

A feedback job decides whether a suggested change is worthwhile in light of the issue scope, correctness, and maintainability.
For worthwhile feedback, make the change, validate and push it, then reply with evidence.
For feedback that warrants no change, explain why and resolve the thread where GitHub supports resolution.
Also resolve addressed threads after recording the implemented fix and its evidence.
Ordinary PR comments have no thread-resolution operation; reply and record their disposition instead.
Do not delete comments or dismiss formal reviews.
A formal request for changes remains a separate merge blocker until GitHub records its appropriate resolution.

Reconsider a resolved thread's change request only when someone explicitly unresolves it.
A new comment in a resolved thread still receives a reply, but does not by itself restart the settled repair.
When the thread is reopened, read the current discussion and handle it again rather than automatically repeating the old resolution.
Ambiguous requirements or feedback that cannot be addressed safely require a human decision.

Persist feedback identities, revisions, thread states, decisions, replies, and resolution actions.
GitHub has no single monotonic cursor across PR comments, reviews, and thread changes.
Build a canonical feedback fingerprint from stable identities, authors, creation and update timestamps, resolution or review state, and body hashes, sorted by type and identity.
Record action intents for replies and resolutions so a crash cannot cause repeated posting on unchanged feedback.
Approval-only votes and reactions are merge-gate facts, not comments that need invented replies.

Any new external feedback must be accounted for before automatic merging.
Persist the quiet-period start from publication, the latest push, or the latest new or materially changed external feedback, whichever is later.
A reopened thread counts as new feedback.
The default quiet period is one hour, configured by `merge_quiet_seconds`.
Runner replies, bookkeeping, and unchanged polling results do not restart that clock.
The quiet period allows time for comments but is not proof that someone reviewed the PR.

## 12. Merge and issue-closure rules

Immediately before every merge, refresh all relevant GitHub facts.
The configured autonomy level must permit that class of merge.
The PR must be open, not draft, created by the runner, and target the configured branch.
Its current delivery and requirement scope must still be valid, and it must have no active coding job.
Configured local validation must have passed for that exact head and current validation configuration, and the latest base must already be incorporated.
All required checks, applicable GitHub approval rules, and conversation requirements must be satisfied.
Every external comment must have a verified posted response and a recorded disposition, and the configured quiet period must have elapsed.
There must be no unresolved human request for changes, unresolved human thread, or merge conflict.
Require server-enforced strict up-to-date protection with at least one real required check on the target branch.
Combine applicable classic branch protections and rulesets using their effective requirements.
The authenticated actor must not be exempt from the required protection.
If effective rules, enforcement, or bypass behavior cannot be established, leave merging to a person.
Do not require an additional human approval when GitHub does not require one.

Use a merge operation that checks the expected head SHA.
Do not use an administrative bypass or ask an agent whether bypassing a rule seems reasonable.
If GitHub rejects the merge, reconcile again and show the actual blocker.
Unknown mergeability or unreadable required-rule information is not permission to proceed.

An expected-head check does not let the client pin an exact base SHA or freeze issue requirements and comments.
Strict protection enforces base up-to-dateness on GitHub, but cannot make the runner's local scope and feedback checks atomic with a merge.
Accept this remaining last-second race rather than claiming an impossible guarantee.
Recheck relevant facts after the merge; if requirements, membership, or feedback changed incompatibly, preserve the merge evidence, flag the discrepancy, and leave the issue open.
Never automatically revert a completed merge to hide that race.
Configuring repository protections or merge queues is outside version one.

After either a human or the runner merges a child, confirm the merged PR, resulting commit, recorded base, and delivery scope through GitHub and Git.
Close the child explicitly as completed and link the merge evidence.
This is necessary because merging into a non-default feature branch may not automatically close its issue.
If closure fails, retry the closure only; never reimplement or remerge the child.
Closing the issue then triggers immediate dependency reconciliation.
Completion bookkeeping is allowed at every autonomy level, including `create_pr`.
Cleanup has its own recoverable transitions and cannot erase the evidence needed for closure or dependency checks.

## 13. Parent integration and completion

A parent becomes integration-ready when every selected child is either verifiably complete or explicitly excluded from scope, with no outstanding child work or unresolved blocking relationships.
Refresh the child list before making that decision.
Record its membership so a newly added or reopened child invalidates integration readiness.
If a removed or excluded child already has code in the feature branch, pause integration and ask the developer whether that code should remain or be reverted through explicit work.
Removing an issue from scope does not authorize silently retaining or reverting its code.

Publish one integration PR from the feature branch to main for the current parent delivery, including at `create_pr` autonomy.
Do not adopt an externally published integration PR; observe it as a blocker or completed integration evidence instead.
Use a separate owned integration worktree and validate the aggregate feature, not just individual children.
Apply the same comment handling, quiet period, CI, approval, and optional-merge gates.
Freeze new child launches only while the current selected scope is integration-ready.
New, reopened, or generated repair children return the parent to child-work mode and block its integration PR.

If main has advanced, use a normal non-rebasing branch update to incorporate it into the feature branch.
This is an authorized integration-maintenance write, not permission to implement child tickets directly on that branch.
Revalidate after the update and restart the quiet period after its push.
If branch protection disallows the update, or feature-to-main conflicts require judgment, mark the parent `needs_human`.
Automatic feature-to-main conflict repair is out of scope for version one.

For concrete integration defects, automatically create a narrowly scoped follow-up child issue and return the parent to child-work mode.
Derive the ticket from the actual blocking CI, validation, or worthwhile comment findings, with reproduction evidence and acceptance criteria.
This is a bounded repair-ticket exception, not permission to plan new features or invent requirements.
Use a recorded creation intent and stable finding identity to avoid duplicate repair children.
Track it as an explicit addition to the effective child list in SQLite and on GitHub where native membership is used, without rewriting the configuration file.
Any prerequisites must remain within that feature's DAG.
Do not quietly fix unrelated defects directly on the feature branch.
The existing integration PR can remain open, but it cannot merge until the new child work is complete and aggregate validation and all other gates pass again.

Merge the integration PR using a merge commit.
If repository policy requires a different method, stop and report the incompatibility instead of silently changing the policy.
Close the parent only after confirming that merge and rechecking that its selected child set is complete.
If an external actor adds a child during the final merge/closure window, leave the parent open and surface the discrepancy.
If feature work was already integrated externally, verify the integration PR or relevant ancestry rather than creating an empty PR.
An empty parent or a manually closed parent is not by itself evidence of successful integration.

If a delivered child reopens, allocate a fresh child delivery and invalidate its historical completion for new dependency decisions.
Pause and recheck unmerged transitive dependents until the new prerequisite delivery is available.
Cancel affected running jobs, preserve their work, and update and revalidate those deliveries after the prerequisite completes.
Do not automatically reopen dependent issues whose deliveries already merged.
If the enclosing parent was previously verifiably delivered and closed, reopen it automatically and allocate a new parent delivery.
A reopened delivered parent also gets a new integration PR when its current scope is ready, using the retained feature branch.
Early manual parent closure without verified delivery still pauses work rather than triggering automatic reopening.
Keep historical deliveries, PRs, and merge evidence; never mutate their records to pretend that they are the new delivery.

## 14. Local persistence, recovery, and limits

### Storage layout and authoritative records

Use one SQLite database for the repository, not one database per feature.
Feature IDs separate task graphs and enforce their independence inside that database.
Store large job inputs, logs, and evidence as files beneath the repository's runner runtime root, with paths and content identities recorded in SQLite.
Git owns the worktree files and commits; a database row describing a worktree does not replace it.

The logical schema must explicitly cover the following records.
Use typed fields and relational keys for known structures rather than one opaque serialized graph or job-state blob.

| Records | Durable contents |
| --- | --- |
| Repository and features | Canonical identity, configuration identity, parent issues, feature branches, discovery modes, and last-served scheduling position |
| Graph snapshots and tasks | Complete observed membership, per-feature task IDs, child issues or integration-node kind, source revisions, and snapshot completeness |
| Dependency edges | Feature ID, prerequisite task ID, dependent task ID, and source relationship identity |
| Deliveries and PRs | Issue identity, delivery number, current scope, PR identity, exact head/base, creation intent, ownership, completion, and release history |
| Worktrees and branches | Delivery owner, canonical path, local/remote refs, expected tips, and cleanup state |
| Jobs and processes | Job inputs, scope fingerprint, process creation identity, session identity, lifecycle, result, and log paths |
| Feedback and problem history | Comment revisions, thread state, dispositions, reply identities, unresolved problem identity, and verified no-progress streak |
| Validation and merge evidence | Exact commits and trees, configuration fingerprint, command outcomes, checks, merge results, and evidence paths |
| Actions and controls | Mutation intents, idempotency keys, observed outcomes, ambiguity, pauses, retries, and ownership release requests |
| Timers and qualification | Persisted CI and quiet-period deadlines, live exercise receipts, tool/runtime identities, and relevant policy fingerprints |

Enable foreign keys on every SQLite connection.
Use composite keys and foreign keys to keep both endpoints of every edge in the same feature snapshot.
Reject self-edges and duplicate edges; detect longer cycles in graph logic rather than assuming foreign keys prove acyclicity.
Use explicit schema migrations, short transactions, and durable journaling; do not disable synchronization to gain throughput.
Do not place secrets in manifests, fingerprints, or configuration snapshots.
Commit complete fetched graph snapshots atomically, retaining the prior complete snapshot if a read fails.
An old snapshot may explain status, but incomplete current dependency reads still block affected mutations.

### Disposable in-memory graphs

Use Python's standard-library `graphlib.TopologicalSorter` for cycle checks and dependency-ready sets.
Its input maps each node to its predecessors; for `A -> B`, B lists A as a predecessor.
Persist tasks, edges, delivery state, and actions, not a pickled sorter or an in-memory ready queue.
Rebuild the sorter after restart or graph changes from a complete, reconciled feature snapshot.
Only verified current completion or an allowed explicit exclusion/acknowledgement satisfies a node.
An exclusion satisfies parent integration scope, not an implementation edge that still requires that excluded task.
Such an edge remains blocked until the developer removes it, as described in section 6.
`get_ready()` does not authorize starting every returned node; apply capacity, ownership, pause, and fairness rules afterward.
Marking a node dispatched or observing an agent exit must never be treated as dependency completion.
Retain enough graph information to show cycles and their downstream blockers while continuing unaffected work.
No additional graph package or graph database is required.

### Coordination and crash recovery

Use an operating-system-held exclusive file lock keyed to the repository's canonical Git common directory.
The lock prevents two runner invocations with different configuration files from operating on the same local repository.
A read-only `status` command does not need the mutation lock.
Operator commands can submit transactional SQLite control requests while the runner holds the lock.
The lock-holding executor applies those requests before its next external action, so `pause` can work without authorizing a second executor.
Separate clones on different machines are not coordinated.
Concurrent coding jobs are owned children of that one executor, not exceptions to the repository lock.

Use SQLite transactions for jobs, ownership, graph snapshots, action intents, feedback, evidence, and pauses.
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
Reconcile owned process identity and unfinished mutations before rebuilding graph readiness or dispatching replacement jobs.
SQLite commits are atomic across process interruption, but GitHub requests and local database writes are not one atomic transaction.
An ambiguous remote outcome requires inspection or human intervention, not blind replay.
Recover interrupted comment posting, repair-issue creation, branch deletion, and worktree removal as carefully as PR creation and merge.
Losing an in-memory sorter is harmless because its durable inputs survive; losing the database or the disk is not covered by this restart guarantee.

### Live qualification

Store live-qualification receipts in SQLite.
A receipt records the disposable repository and exercise run, observed child and parent transitions, tool versions, runner runtime identity, relevant policy configuration, completion time, and retained evidence paths.
The `merge_children` level requires a successful exercise through child merge and restart recovery.
The `merge_parents` level requires the full dependency, parallel-work, feedback, restart, and parent-integration exercise.
Changed runner runtime code, tool versions, or safety-affecting review/merge policy invalidates qualification and disables automatic merging until a new live exercise succeeds.
Documentation-only, logging, and polling-interval changes do not invalidate it.
No reviewer version or reviewer-specific receipt is involved.
The normal `doctor` compatibility rules may still block all mutating work when a required interface cannot be trusted.

### Process supervision and progress limits

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
| Active child deliveries | Five per feature, including waiting PRs |
| Required CI waiting | Flag for help after one hour; resume automatically if the check later passes |
| Automatic-merge quiet period | One hour since publication, the latest push, or new external feedback |
| Infrastructure retries | Two retries with one-minute and five-minute delays |
| Repeated no-progress result | Stop after two attempts repeat the same unresolved failure without verified progress |
| Rate limiting | Honor GitHub's retry or reset time without spinning |

There are no total-job, per-run-job, or repair-job count limits.
The active-slot limit bounds simultaneous work, not total work.
Verified progress means a previously failing required check or validation command passes, or a blocking finding is cleared through recorded review feedback.
New commits, changed logs, an agent's confidence, or the runner's own thread resolution alone do not prove progress.
Carry unresolved problem identity and its no-progress streak across restarts, head changes, new repair tickets, and delivery numbers.
Do not escape a stuck repair by creating a new ticket for the same problem.
An explicit operator retry can reset a no-progress stop after review; record the reason.
These limits do not promise a monetary cost cap, and continuing useful work may consume arbitrarily many jobs.

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
| `run` | Reconcile and supervise parallel jobs until interrupted or globally blocked |
| `run --once` | Complete at most one selected transition, then exit |
| `status` | Show per-feature DAGs, slot usage, deliveries, PRs, worktrees, jobs, feedback, timers, cleanup, and precise blockers |
| `pause` / `resume` | Persistently pause or resume all work or a selected parent/child |
| `release` | Explicitly stop managing a delivery and free its slot without closing the issue or deleting its work |
| `acknowledge` | Record a reason for treating an already completed no-code issue as satisfied |
| `retry` | Resume explicitly released or abandoned work, or clear a no-progress stop, after operator review and capacity checks |
| `qualify` | Run the separately authorized live compatibility exercise and record its evidence for autonomy promotion |

Every command accepts an explicit configuration path.
`run --once` may wait for one selected coding job to finish; it must supervise that job and not leave it orphaned.
It does not start a hidden continuous run or fill all parallel slots before exiting.
`plan` may perform remote reads but does not run validation commands, fetch into the working repository, clean up files, or change runner action state.
Do not apply a dry-run flag only to merges while allowing other mutations.
An explicit release waits for any owned job to stop safely before relinquishing its slot.
Released PRs are not monitored or modified until explicitly resumed through `retry`; they remain visible as released history.

Show one concise line per meaningful transition, including the parent, issue or PR, action, result, and log path.
Do not dump full prompts or large diffs into the console by default.
Keep detailed stdout and stderr in job-specific log files.
Print a heartbeat while waiting so the process does not look hung.

The first Ctrl+C stops dispatch and requests graceful cancellation of all active owned jobs.
A second Ctrl+C terminates only their owned subprocess trees.
Preserve all work and record the interrupted action for reconciliation on restart.
Pausing through another CLI invocation stops new transitions but does not silently kill an active job.
Recheck pauses and ownership before every push, PR or issue creation, comment action, merge, issue state change, and cleanup.

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

Keep narrow internal seams for GitHub/Git execution, supervised subprocesses, SQLite persistence, graph readiness, and feedback normalization.
Use real adapters in normal runs and deterministic fakes in tests.
Use typed dataclasses or equivalent validated records instead of passing nested untyped dictionaries throughout the program.
Use standard-library TOML, SQLite, and `graphlib` support.
Do not add a general plugin system, arbitrary workflow DSL, event bus, or backend registry.
The coordinator can dispatch and collect independent jobs without making `decide_next` asynchronous or stateful.
Graph calculations do not replace merge verification, active-slot accounting, action intents, or recovery.
Locking, ownership, intents, safe cancellation, and restart recovery must exist before any slice mutates a real repository.
Earlier demonstrations without those safeguards are restricted to disposable test fixtures.

The existing `orchestrator` integration tests provide useful precedent for lifecycle and bounded-loop scenarios.
This runner needs additional tests through its own public reconciliation interface and real temporary Git repositories.
Tests must assert observed actions and preserved state, not private helper call counts.

## 17. Acceptance and testing

Implement each behavior test-first through the highest useful interface.
Most coverage should use a fake GitHub adapter, fake agent executables, a controllable clock, SQLite, and actual temporary local Git repositories.
No ordinary automated test should require paid model calls or mutate a real repository on GitHub.

| Scenario | Required outcome |
| --- | --- |
| Fresh child | Worktree starts at the latest remote feature SHA, not stale local main |
| Dependency chain | The next child starts only after its prerequisite is merged and available in its base |
| Edge direction | `A -> B` blocks B on A, not A on B |
| Independent feature DAGs | Cross-feature and cross-repository edges are rejected rather than coordinated |
| Cycle introduced | Cycle and downstream work stop; unrelated ready tasks can continue |
| Parallel ready tasks | Independent issues use distinct worktrees and can run concurrently |
| Per-feature capacity | Five active children per feature by default; another feature has its own five slots |
| Waiting PRs | Waiting consumes a slot; repairs reuse it; a sixth child does not start |
| Reopened prerequisite at capacity | Pause the affected feature until the operator releases a slot |
| Repeated idle polls | No duplicate job, comment, branch, PR, or repair issue |
| Reused delivery worktree | Implementation and repair jobs reuse one worktree without concurrent writers |
| Base or head changes | Validation is invalidated, CI and GitHub approvals are rechecked, and pushes restart the quiet period |
| Validation changes tracked files | Zero exit is rejected as validation of the committed head |
| Validation configuration changes | Old validation cannot authorize publication or merge readiness |
| Optional failing or pending check | Visible but does not block or trigger paid repair |
| `create_pr` autonomy | Publish and maintain PRs, observe human merges, and close verified issues, but never select or execute a merge |
| `merge_children` autonomy | Eligible child PRs may merge, but integration PRs cannot merge |
| Externally published PR | No adoption, comment response, repair, thread resolution, or runner merge |
| No reviewer installed | Setup and normal operation require no reviewer executable or local artifacts |
| Missing or stale qualification receipt | Autonomy promotion is refused and prohibited merge transitions remain unavailable |
| Missing strict protection or unreadable rules | Publish and maintain PRs, but block automatic merging |
| No GitHub approval requirement | No extra approval is invented; all other gates and the quiet period still apply |
| Quiet period | No automatic merge before the configured hour; pushes and new feedback restart it, runner replies do not |
| Required check missing or failed | No merge |
| Required CI stalls then passes | Flag after one hour, preserve timer across restart, and resume automatically after recovery |
| Worthwhile comment | Implement, validate, reply with evidence, and resolve where supported |
| No-change or informational comment | Reply with the reason or acknowledgement and record disposition; do not invent code changes |
| Resolved thread gets another comment | Reply but do not restart settled repair unless the thread is explicitly reopened |
| Human request for changes | Address feedback but never dismiss the formal review |
| Same-account human comment | Respond unless its identity matches a recorded runner output |
| Crash after PR or reply creation | Recover the exact recorded operation rather than duplicating it |
| Crash after merge | Restart closes the issue without another implementation or merge |
| Last-second scope or feedback change | Preserve the merge, flag the discrepancy, and leave the issue open rather than automatically reverting |
| Crash during SQLite transaction | No partial snapshot or half-written ownership transition becomes actionable |
| Lost in-memory graph | Rebuild from reconciled durable records; no duplicate dispatch or premature dependency completion |
| Dirty developer checkout | Existing files remain unchanged |
| Conflicting worktree or branch | Stop that issue without overwriting or deleting anything |
| Normal human commits | Preserve and incorporate them after the active job, then revalidate |
| Human dirty edits or rewritten history | Stop rather than silently overwrite or force-push |
| Second runner | Mutation lock is refused clearly |
| Agent timeout or ambiguous orphan | Preserve work and do not launch a competing agent |
| Requirement changes mid-job | Detect on normal poll, cancel only affected jobs, preserve work, and restart against latest scope |
| Unrelated sibling added | Invalidate parent readiness without cancelling unaffected child jobs |
| Two no-progress repairs | Stop for help; new commits, tickets, delivery numbers, or restart cannot erase the same problem's streak |
| Continued verified progress | No repair-count or per-run job cap stops otherwise eligible work |
| Parent children finish | Publish and maintain an integration PR; do not close the parent early |
| New or reopened child | Parent readiness is invalidated; unmerged dependents wait for the new delivery |
| Previously delivered parent | Reopen automatically when a child reopens and create a fresh integration delivery |
| Integration defect | Create one grounded repair child, include it in either discovery mode, and leave TOML unchanged |
| Removed child's code remains | Pause integration for an explicit decision |
| Parent merge confirmed | Close the parent and retain all merge evidence |
| Safe cleanup after squash merge | Remove only the verified completed worktree and unchanged owned child refs |
| Dirty worktree or advanced remote child ref | Preserve unexpected work and report a cleanup blocker |
| Crash during cleanup | Reconcile exact paths and refs without deleting new or unrelated work |
| Completed feature cleanup | Retain feature branches, logs, database history, and qualification evidence |
| Read-only plan | No validation command, paid job, local Git write, or GitHub mutation |
| Windows paths with spaces | Interpreter startup and argument passing work without shell interpolation |

Before calling the tool usable, run one explicit end-to-end exercise in a developer-authorized disposable GitHub repository.
Use real coding-agent invocations and a feature with a dependency chain plus an independent ready child to demonstrate parallel work.
Exercise a worthwhile comment, a no-change resolution, an explicitly reopened thread, blocking CI, a push restarting the quiet period, a restart after a merge, and final feature integration.
Supply feedback explicitly in the exercise; do not introduce an external reviewer dependency.
Confirm actual files, commits, PR bases, applicable GitHub approvals, issue closures, safe cleanup, and retained evidence.
The developer authorizes this paid and mutating exercise separately from ordinary automated tests.
The `qualify` command records the resulting compatibility receipt and retained evidence.
Automatic child or parent merging remains unavailable until the corresponding portion of this exercise has succeeded for the installed toolchain.
Qualification uses a dedicated, explicitly authorized execution path scoped to the configured disposable repository.
It does not temporarily raise the autonomy of ordinary reconciliation against the developer's real repository.

## 18. Suggested implementation slices

1. Build configuration, version and interface checks, `doctor`, and complete read-only GitHub snapshots without reviewer dependencies.
2. Add SQLite task/edge records, independent feature DAGs, `graphlib` readiness, scope manifests, and read-only `plan`.
3. Establish ownership, repository locking, action intents, process supervision, pauses, and crash recovery before any real-repository mutation.
4. Complete one child through its isolated delivery worktree, a bounded coding job, a structured result, and exact-tree local validation.
5. Add recoverable push and PR creation, then prove the publish-and-maintain boundary without automatic merges.
6. Add per-feature active slots, concurrent independent jobs, normal human-commit reconciliation, and fair cross-feature scheduling.
7. Add owned-PR comment responses and resolutions, blocking-CI repairs, persisted timers, and cross-delivery no-progress tracking.
8. Observe human merges, close issues, advance dependencies, and handle scope changes and reopened deliveries.
9. Add aggregate integration validation, tool-published parent PRs, explicit repair children, and removed-scope decisions.
10. Add safe, recoverable worktree and child-branch cleanup while retaining feature branches and audit evidence.
11. Add optional merge selection and execution gates, strict protection checks, GitHub approvals, and the quiet period, initially disabled.
12. Run the separately authorized disposable-repository qualification exercise and enable staged automatic merging only with valid evidence.

Each slice should have a runnable vertical outcome rather than delivering isolated infrastructure first.
Use `/to-tickets` to split these slices further where needed into self-contained tickets with explicit blocking edges, then use a fresh `/implement` session per ticket.
Preserve this document as the shared spec.
An implementation session's own code review is distinct from the product, which does not execute reviewers.

## 19. Deliberate exclusions and remaining limits

This version does not plan features, write specs, triage unrelated tickets, or interview the developer.
Its only automatic ticket creation is a narrowly scoped integration-repair child grounded in observed findings.
It does not run reviewers, require reviewer-local artifacts, adopt externally published PRs, dismiss formal reviews, or delete comments.
It does not support multiple machines, fork PRs, cross-feature dependencies, cross-repository dependencies, or nested issue hierarchies.
It does not install branch protections, manage deployment credentials, deploy applications, or automatically merge feature-to-main conflicts.
It does not create or delete Git worktrees outside its owned worktree root.
It does not automatically delete feature branches, unexpected work, logs, audit records, or unrelated Copilot sessions.
It does not promise to defeat malicious repository code, provide exact dollar budgets, or guarantee atomicity across GitHub and SQLite.
It does not treat an in-memory graph as persistence or promise recovery after loss of its database or disk.

The important pre-build compatibility checks are the repository's feature-branch and main policies, GitHub's structured interfaces, durable local recovery, and noninteractive Copilot permissions.
All have explicit stop conditions.
There is no requirement to solve distributed scheduling or production operations before this becomes useful locally.

## 20. Design references

- [Ask Matt flow](../../.agents/skills/ask-matt/SKILL.md) for the spec-to-tickets-to-implementation workflow.
- [Phase boundaries](../../.agents/skills/ask-matt/PHASE-BOUNDARIES.md) for fresh implementation contexts and retained primary sources.
- [Existing orchestrator internals](../../orchestrator/README.md) for the current, separate PR Flywheel design.
- [Existing lifecycle tests](../../orchestrator/tests/test_integration.py) for bounded-loop testing precedent.
- [Python `graphlib`](https://docs.python.org/3.11/library/graphlib.html) for dependency ordering, cycle detection, and ready-node processing.
- [SQLite atomic commits](https://www.sqlite.org/atomiccommit.html) for transaction behavior across interruption.
- [SQLite foreign keys](https://www.sqlite.org/foreignkeys.html) for relational integrity and connection-level enforcement.
- [GitHub protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches#require-status-checks-before-merging) for strict up-to-date checks.
- [GitHub rule layering](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets#about-rule-layering) for combined ruleset and branch-protection requirements.
- [`gh pr merge`](https://cli.github.com/manual/gh_pr_merge) for expected-head merge checks.
