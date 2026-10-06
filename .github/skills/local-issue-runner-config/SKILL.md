---
name: local-issue-runner-config
description: >
  Create a validated Local Issue Runner TOML configuration from a GitHub parent issue.
  Use when asked to configure the Local Issue Runner, generate its TOML file, prepare
  an issue for a runner invocation, or turn an issue URL into runnable local configuration.
---

# Local Issue Runner Configuration

Create a Local Issue Runner TOML configuration for one GitHub parent issue.

The final file is written only after the repository-scoped `issue-readiness` skill has evaluated the issue, candidate configuration, target repository, and execution environment.

This skill creates configuration only.
It must not start the runner, create branches, push commits, open pull requests, or modify GitHub issues.

## Required first action

Invoke the `issue-readiness` skill before writing a final TOML file.

Use the Skill tool with:

```text
issue-readiness
```

Follow all readiness gates from that skill.
The candidate configuration may be held in memory or written to session-scoped temporary storage when `doctor` and `plan` require a file.
A temporary candidate is not the final requested configuration and must be removed after the check.

Do not create the final TOML when the readiness verdict is `NOT READY` or `INCOMPLETE CHECK`.
When the verdict is `READY WITH WARNINGS`, summarize the warnings and require explicit user confirmation before writing the final file.
When the verdict is `READY`, write the file without an additional confirmation unless an existing file would be overwritten.

## Inputs

Require:

- A GitHub parent issue URL, or an unambiguous `owner/repository#number` reference.
- The path to an existing local clone of the issue repository.
- An existing remote feature branch for the parent.

Accept optional overrides for:

- Output path.
- Git remote.
- Main branch.
- Selected child issues.
- Validation commands.
- Required checks.
- Autonomy.
- Poll and timeout values.
- Maximum active issues.

Ask the user for any material input that cannot be derived unambiguously.
Never guess between multiple local clones, remotes, feature branches, or materially different validation strategies.

## Defaults

Use these defaults unless the user or repository provides a more authoritative value:

```toml
version = 1
remote = "origin"
poll_seconds = 300
agent_timeout_seconds = 3600
max_active_issues_per_feature = 5
ci_wait_timeout_seconds = 3600
merge_quiet_seconds = 3600
autonomy = "create_pr"
```

Default to `autonomy = "create_pr"`.
Do not select `merge_children` or `merge_parents` unless the user explicitly requests automatic merging and the readiness skill confirms the required protections, checks, approvals, and qualification gates.

Omit `child_issues` by default so the runner uses all native GitHub sub-issues.
Include `child_issues` only when the user explicitly requests a subset.

## Workflow

### 1. Invoke issue readiness

Load the `issue-readiness` skill before generating the final configuration.

Provide it with:

- The parent issue.
- The target clone.
- The intended feature branch.
- The intended output or temporary candidate path.
- The proposed validation commands.
- The proposed required checks.
- Any runtime endpoint and authentication prerequisites.

Continue gathering candidate configuration evidence while following that skill's read-only workflow.

### 2. Resolve the GitHub identity

Read the parent issue and derive:

- `repository` in `owner/repository` form.
- The positive integer parent issue number.

Confirm that:

- The issue exists.
- The issue is open.
- The authenticated GitHub account can read it.
- The issue is a parent suitable for native sub-issue discovery.

Use `local_issue_runner validate-parent` through the readiness skill.
Do not use a search-result title or copied issue text as a substitute for the canonical issue.

### 3. Resolve the target clone and remote

Resolve paths from the active session repository rather than changing to hardcoded directories.

For the target clone:

1. Confirm the path exists.
2. Confirm it is a Git worktree.
3. Read the configured remote URL.
4. Normalize the remote URL to a GitHub `owner/repository` identity.
5. Require it to match the issue repository.

Default to `origin` only when that remote exists and matches the issue repository.
If multiple remotes match, ask the user which remote the runner should push to.

Do not include credentials in a remote URL or in the TOML.

### 4. Resolve the main branch

Read the GitHub repository's default branch and the remote's symbolic HEAD.

Use the branch only when those sources agree.
If they disagree, ask the user rather than selecting one silently.

Confirm the main branch exists on the selected remote.

### 5. Resolve the feature branch

The feature branch must already exist remotely.
This skill must not create it.

Use a branch explicitly supplied by the user when it exists and is suitable.
Otherwise inspect remote branches and pull requests for an unambiguous branch associated with the parent issue or its maintained specification.

Ask the user when:

- No plausible branch exists.
- More than one plausible branch exists.
- The current local branch differs from the only plausible remote feature branch.
- The branch appears to serve another parent issue.

Check that the branch name is valid for Git and compatible with the Local Issue Runner's GitHub API handling.
Treat a known runner incompatibility, including unsafe handling of slash-containing branch names, as a readiness blocker rather than silently renaming the branch.

### 6. Derive validation commands

Validation commands are mandatory and must be arrays of executable arguments.
The runner launches them without a shell.

Derive commands from authoritative sources in this order:

1. Target-repository contributor instructions.
2. Target-repository README files.
3. Package-manager manifests and workspace configuration.
4. Pull-request workflow definitions.
5. Existing maintained automation scripts.

Prefer the smallest complete command set that covers the parent issue's likely changes while keeping the feature branch green after every child.
Use full repository validation when children can affect shared contracts or multiple workspaces.

Before accepting a command:

- Verify the executable exists.
- Run its help, collection, dry-run, or smallest documented safe startup form.
- Confirm the actual configured command passes on the remote feature-branch baseline.
- Confirm it does not require an interactive shell.
- Confirm it does not contain pipes, redirection, command chaining, environment assignment, aliases, or shell built-ins.
- Confirm it does not mutate production resources.

Do not weaken validation to work around a broken baseline.
Do not use `--no-sync`, skipped tests, narrowed selectors, or ignored exit codes unless that is the repository's documented normal validation contract.

Ask the user to choose when multiple materially different valid command sets remain.

### 7. Derive required checks

Read effective GitHub rules for the main branch.
Collect the exact names of required status checks.

Use the effective-rules endpoint with the branch name correctly URL encoded.
Account for repository and inherited organization rulesets.

Populate `required_checks` with unique exact check names in stable sorted order.
Do not invent names from workflow filenames or job display names when GitHub reports authoritative contexts.

An empty list is valid only when:

- GitHub reports no required checks; or
- The selected `create_pr` workflow intentionally does not rely on automatic merge qualification.

Even with `create_pr`, prefer recording authoritative required checks so status and future autonomy changes remain accurate.

### 8. Derive concurrency and timing

Keep the documented timing defaults unless the user has an operational reason to override them.

Set `max_active_issues_per_feature` no higher than the number of independent issues in the initial or expected frontier.
Keep the default value of `5` when the graph supports at least five concurrent children or when future frontier width is uncertain.

Reduce concurrency when:

- Children modify the same central contract.
- The repository has scarce external test resources.
- Live evaluation consumes a rate-limited deployment.
- Parallel worktrees would contend for a non-isolated local service.

Ask the user before materially increasing timeouts, polling frequency, or concurrency.

### 9. Build a temporary candidate

Create a candidate TOML in session-scoped temporary storage.
Never place the temporary file in the target repository.

Use the exact version 1 schema:

```toml
version = 1
repository = "owner/repository"
repository_path = 'C:\path\to\clone'
remote = "origin"
main_branch = "main"
poll_seconds = 300
agent_timeout_seconds = 3600
max_active_issues_per_feature = 5
ci_wait_timeout_seconds = 3600
merge_quiet_seconds = 3600
autonomy = "create_pr"

validation_commands = [
  ["executable", "argument"],
]

required_checks = [
  "required-check",
]

[[parents]]
issue = 123
feature_branch = "feature-branch"
```

Use a TOML literal string for an absolute Windows `repository_path`.
If a relative path is clearer and stable, calculate it relative to the final TOML file, not the current shell directory.

Do not include comments that may become stale.
Do not include secrets or environment-variable values.

When targeting all native sub-issues, omit `child_issues`.
When targeting an explicit subset, include the validated positive issue numbers:

```toml
child_issues = [124, 125]
```

### 10. Validate the candidate

Use the candidate as input to the `issue-readiness` skill.

At minimum, that skill must successfully complete:

```powershell
uv run python -m local_issue_runner validate-parent <parent-issue-url> --json
uv run python -m local_issue_runner doctor --config <temporary-config>
uv run python -m local_issue_runner plan --config <temporary-config>
```

The readiness review must also assess issue content, decomposition, repository baseline, and runtime prerequisites.

Do not treat valid TOML parsing as sufficient.
Do not create the final file if `doctor` or `plan` fails.

### 11. Choose the output path

Use the user-supplied path when provided.

Otherwise recommend:

```text
<runner-repository>\local-issue-runner.<repository-name>-<parent-issue>.toml
```

This avoids silently replacing a configuration for another feature.

Before writing:

- Resolve the absolute output path.
- Confirm its parent directory exists or create only the specific required directory.
- Check whether the file already exists.
- If it exists, compare contents and ask before replacing it.
- Do not stage or commit the machine-specific configuration.
- Warn when the output path is not ignored by Git.

### 12. Write and revalidate the final file

Write the final TOML only after the readiness gate permits it.

Run `doctor` and `plan` again using the final path because relative `repository_path` resolution depends on the TOML location.
If either command behaves differently from the temporary candidate, remove the newly written final file and report the failure.
Never leave behind a success-shaped configuration that was not validated from its final location.

Remove the temporary candidate after final validation or after any failure.

## Output

Lead with whether the file was created.

When successful, report:

- Final absolute path.
- Parent issue.
- Target repository and feature branch.
- Native discovery or explicit child selection.
- Autonomy.
- Validation commands.
- Required checks.
- Ready frontier from `plan`.
- Readiness verdict.

When blocked, do not create the final file.
Report:

- The readiness verdict.
- Each blocker with direct evidence.
- The smallest complete remediation.
- Any user decision needed before retrying.

Do not print secrets or full environment-variable values.
