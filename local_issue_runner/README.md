# Local Issue Runner

The Local Issue Runner coordinates implementation work from GitHub parent and child issues on one local machine.
It discovers issue dependencies, schedules ready work, persists state in SQLite, and uses isolated Git worktrees for runner-owned deliveries.

## Requirements

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/)
- Git
- GitHub CLI (`gh`), authenticated with `gh auth login`
- GitHub Copilot CLI, authenticated with `copilot auth login`
- An existing local clone of the target repository
- Existing remote feature branches for each configured parent issue

Run all commands from this repository's root so Python can import the `local_issue_runner` package.

## Configure issue input

The runner receives GitHub issue input from a TOML configuration file.
The `run` command does not currently accept an issue number or issue URL directly.

Create a file such as `local-issue-runner.toml`:

```toml
version = 1
repository = "owner/repository"
repository_path = '..\repository'
remote = "origin"
main_branch = "main"
poll_seconds = 300
agent_timeout_seconds = 3600
max_active_issues_per_feature = 5
ci_wait_timeout_seconds = 3600
merge_quiet_seconds = 3600
autonomy = "create_pr"
delivery_mode = "child_prs"

validation_commands = [
  ["uv", "run", "pytest"],
]

required_checks = []

[[parents]]
issue = 100
feature_branch = "feature/issue-100"
child_issues = [123]
```

Replace the example values as follows:

| Input | Meaning |
| --- | --- |
| `repository` | Target GitHub repository in `owner/repository` form. |
| `repository_path` | Path to its existing local clone, resolved relative to the TOML file. |
| `issue` | Parent GitHub issue that represents the feature. |
| `feature_branch` | Existing remote branch into which child issue work will be integrated. |
| `child_issues` | Explicit child GitHub issue numbers to run, such as issue `123` above. |
| `delivery_mode` | `child_prs` for one pull request per child, or `local_commits` for sequential commits on the feature branch. |
| `validation_commands` | Commands that must validate changes in the target repository. |

To run all native GitHub sub-issues of parent issue `100`, omit `child_issues`:

```toml
[[parents]]
issue = 100
feature_branch = "feature/issue-100"
```

To target one specific child issue, include only that issue in `child_issues`.
The issue must belong to the configured feature scope, and a child issue cannot belong to more than one configured parent.

## Run locally against a GitHub issue

First, validate that a GitHub parent issue has the one-level sub-issue and dependency structure required by this runner:

```powershell
uv run python -m local_issue_runner validate-parent https://github.com/microsoft/fde-aa-agency/issues/342
```

Use `--json` for a machine-readable version 1 report:

```powershell
uv run python -m local_issue_runner validate-parent https://github.com/microsoft/fde-aa-agency/issues/342 --json
```

The JSON contract is defined by `local_issue_runner\schemas\parent_issue_validation.schema.json`.
The command uses authenticated, read-only `gh api graphql` queries and follows all sub-issue and dependency pages.
It reports exact suggested issue changes when the parent, children, or prerequisite graph is not ready.
Exit status `0` means the issue structure is valid, `1` means the structure is invalid, and `2` means the URL, GitHub retrieval, or local tool invocation failed.
This command validates GitHub issue structure only.
It does not validate the local repository, tools, authentication compatibility, configuration, branches, or runner state.

After issue structure validation, perform the read-only local compatibility check:

```powershell
uv run python -m local_issue_runner doctor --config .\local-issue-runner.toml
```

Then preview the configured issue graph and next transition without mutating GitHub or Git:

```powershell
uv run python -m local_issue_runner plan --config .\local-issue-runner.toml
```

Execute at most one transition for the configured issue input:

```powershell
uv run python -m local_issue_runner run --once --config .\local-issue-runner.toml
```

Override the configured delivery mode for one run:

```powershell
uv run python -m local_issue_runner run --once --config .\local-issue-runner.toml --delivery-mode local_commits
```

Run the foreground reconciliation loop continuously:

```powershell
uv run python -m local_issue_runner run --config .\local-issue-runner.toml
```

The continuous runner polls immediately and then waits for `poll_seconds` between reconciliation cycles.
Press `Ctrl+C` to interrupt the foreground process.

## Inspect and control the runner

Show the current plan, coordinator state, and blockers:

```powershell
uv run python -m local_issue_runner status --config .\local-issue-runner.toml
```

Pause or resume all configured work:

```powershell
uv run python -m local_issue_runner pause --config .\local-issue-runner.toml
uv run python -m local_issue_runner resume --config .\local-issue-runner.toml
```

Pause or resume one child issue:

```powershell
uv run python -m local_issue_runner pause --config .\local-issue-runner.toml --parent 100 --issue 123
uv run python -m local_issue_runner resume --config .\local-issue-runner.toml --parent 100 --issue 123
```

Runtime state is stored in `.local-issue-runner\runner.db` inside the configured target repository.

## Local-commit delivery mode

Set `delivery_mode = "local_commits"` when child issues should become commits on the configured feature branch instead of separate child pull requests.
The runner uses a dedicated runner-owned worktree under `.local-issue-runner`, never the developer's checkout.
It processes one child globally at a time because all selected children share the feature branch.

Each successful child must produce exactly one commit.
The commit must be the worktree's final `HEAD`, directly descend from the child's recorded base commit, leave no tracked changes, and pass the configured exact-tree validation commands.
If execution or validation fails, the runner rolls back only its owned feature worktree to the child's recorded base.

The runner stores local completion evidence before closing the child issue.
The closure message identifies the commit and feature branch.
If closing the issue fails, reconciliation retries the closure without rerunning the implementation or creating another commit.
A closed issue without matching durable local completion evidence does not unlock dependent work.

Commits remain local while the selected child batch is running.
After every selected child has verified completion evidence, the runner pushes the feature branch once and then continues with the existing feature-branch-to-main integration pull request workflow.
The push is never forced.
If the remote feature branch advances during the batch, the runner blocks rather than merging, rebasing, resetting, or overwriting external work.
An interrupted push is recovered by comparing the expected local head with the observed remote head.

Until publication completes, status output reports the local feature head and pending unpublished work.
Do not delete `.local-issue-runner`, its database, or its owned worktrees while unpublished commits exist.

## Autonomy

Use `autonomy = "create_pr"` initially.
This permits implementation, validation, pushing, and PR maintenance but does not permit automatic merges.

The additional supported autonomy values are `merge_children` and `merge_parents`.
Automatic merging also depends on live qualification and all configured GitHub protection, validation, review, CI, and quiet-period gates passing.

## Development

Run the Local Issue Runner test suite:

```powershell
uv run pytest local_issue_runner\tests
```

Run linting and strict type checks:

```powershell
uv run ruff check local_issue_runner local_issue_runner\tests
uv run pyright local_issue_runner
```
