from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from local_issue_runner.agent_backend import DeliveryAgent
from local_issue_runner.db import CoordinatorStore, SnapshotStore
from local_issue_runner.git_ops import GitOperations
from local_issue_runner.integration import IntegrationPullRequest, IntegrationRequest
from local_issue_runner.local_delivery import LocalCommitPlanner, LocalCommitSupervisor
from local_issue_runner.models import (
    AutonomyLevel,
    DeliveryMode,
    DependencyFact,
    DependencyRead,
    IssueFact,
    ParentConfig,
    RunnerConfig,
    TransitionKind,
)


def git(path: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(path), *arguments),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def remote_head(repository: Path) -> str:
    output = git(
        repository,
        "ls-remote",
        "--heads",
        "origin",
        "refs/heads/feature/100",
    )
    return output.split()[0]


@dataclass
class FakeGitHub:
    initial_head: str
    states: dict[int, str] = field(
        default_factory=lambda: {100: "OPEN", 101: "OPEN", 102: "OPEN"}
    )
    closed: list[int] = field(default_factory=list)
    integration_prs: list[IntegrationPullRequest] = field(default_factory=list)

    def list_sub_issues(self, repository: str, parent: int) -> tuple[int, ...]:
        assert repository == "owner/project"
        assert parent == 100
        return (101, 102)

    def get_issue(self, repository: str, number: int) -> IssueFact:
        assert repository == "owner/project"
        return IssueFact(
            repository,
            number,
            f"Issue {number}",
            self.states[number],
            parent_issue=100 if number in {101, 102} else None,
            body=f"Implement requirement {number}.",
        )

    def read_dependencies(self, repository: str, number: int) -> DependencyRead:
        assert repository == "owner/project"
        if number == 102:
            return DependencyRead(
                complete=True,
                edges=(
                    DependencyFact(
                        repository,
                        prerequisite=101,
                        dependent=102,
                    ),
                ),
            )
        return DependencyRead(complete=True, edges=())

    def get_issue_state(self, repository: str, number: int) -> str:
        assert repository == "owner/project"
        return self.states[number]

    def close_issue_as_completed(
        self, repository: str, number: int, evidence_url: str
    ) -> None:
        assert repository == "owner/project"
        assert "/commit/" in evidence_url
        if self.states[number] != "CLOSED":
            self.states[number] = "CLOSED"
            self.closed.append(number)

    def list_integration_pull_requests(
        self, repository: str, feature_branch: str, main_branch: str
    ) -> tuple[IntegrationPullRequest, ...]:
        assert repository == "owner/project"
        assert feature_branch == "feature/100"
        assert main_branch == "main"
        return tuple(self.integration_prs)

    def create_integration_pull_request(
        self, candidate: object
    ) -> IntegrationPullRequest:
        assert isinstance(candidate, IntegrationRequest)
        from local_issue_runner.integration import integration_pr_ownership_marker

        created = IntegrationPullRequest(
            number=51,
            state="OPEN",
            head_ref=candidate.feature_branch,
            head_sha=candidate.feature_head,
            base_ref=candidate.main_branch,
            body=(
                candidate.body
                + "\n\n"
                + integration_pr_ownership_marker(candidate.identity)
            ),
        )
        self.integration_prs.append(created)
        return created


@dataclass
class CommittingLauncher:
    issues: list[int] = field(default_factory=list)

    def __call__(self, worktree: Path, scope_json: bytes) -> str:
        scope = json.loads(scope_json)
        issue = int(scope["issue"]["number"])
        self.issues.append(issue)
        target = worktree / f"issue-{issue}.txt"
        target.write_text(f"implemented {issue}\n", encoding="utf-8")
        git(worktree, "add", target.name)
        git(worktree, "commit", "-m", f"Implement issue {issue}")
        commit = git(worktree, "rev-parse", "HEAD")
        return json.dumps(
            {
                "outcome": "success",
                "summary": f"Implemented issue {issue}.",
                "commit_ids": [commit],
                "validation_summary": "Validation passed.",
                "feedback_dispositions": [],
                "blockers": [],
            }
        )


def create_repository(tmp_path: Path) -> tuple[Path, str]:
    remote = tmp_path / "remote.git"
    repository = tmp_path / "repository"
    subprocess.run(("git", "init", "--bare", str(remote)), check=True)
    subprocess.run(("git", "clone", str(remote), str(repository)), check=True)
    git(repository, "config", "user.name", "Runner Tests")
    git(repository, "config", "user.email", "runner@example.test")
    git(repository, "checkout", "-b", "main")
    (repository / "README.txt").write_text("initial\n", encoding="utf-8")
    git(repository, "add", "README.txt")
    git(repository, "commit", "-m", "Initial commit")
    git(repository, "push", "-u", "origin", "main")
    git(repository, "checkout", "-b", "feature/100")
    git(repository, "push", "-u", "origin", "feature/100")
    initial = git(repository, "rev-parse", "HEAD")
    git(repository, "checkout", "--detach")
    return repository, initial


def test_local_mode_commits_children_sequentially_then_pushes_once(
    tmp_path: Path,
) -> None:
    repository, initial = create_repository(tmp_path)
    config = RunnerConfig(
        version=1,
        repository="owner/project",
        repository_path=repository,
        remote="origin",
        main_branch="main",
        delivery_mode=DeliveryMode.LOCAL_COMMITS,
        poll_seconds=1,
        agent_timeout_seconds=30,
        max_active_issues_per_feature=5,
        ci_wait_timeout_seconds=30,
        merge_quiet_seconds=30,
        autonomy=AutonomyLevel.CREATE_PR,
        validation_commands=((sys.executable, "-c", "pass"),),
        required_checks=(),
        parents=(ParentConfig(100, "feature/100", (101, 102)),),
        config_path=tmp_path / "runner.toml",
    )
    database = repository / ".local-issue-runner" / "runner.db"
    store = CoordinatorStore(database)
    github = FakeGitHub(initial)
    operations = GitOperations()
    launcher = CommittingLauncher()
    planner = LocalCommitPlanner(
        config=config,
        facts=github,  # type: ignore[arg-type]
        github=github,  # type: ignore[arg-type]
        store=store,
        snapshots=SnapshotStore(database),
        git=operations,
    )
    supervisor = LocalCommitSupervisor(
        config=config,
        facts=github,  # type: ignore[arg-type]
        github=github,  # type: ignore[arg-type]
        store=store,
        git=operations,
        agent=DeliveryAgent(launcher=launcher),
    )

    first = planner.select()
    assert first is not None
    assert first.kind is TransitionKind.START_CHILD
    assert first.issue_number == 101
    supervisor.launch(first)
    assert remote_head(repository) == initial

    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "DELETE FROM action_outcomes WHERE intent_key LIKE 'local-child:%:101:1'"
        )
        connection.execute(
            "UPDATE action_intents SET state = 'running' "
            "WHERE intent_key LIKE 'local-child:%:101:1'"
        )
        connection.commit()
    finally:
        connection.close()
    github.states[101] = "OPEN"
    github.closed.clear()
    interrupted = store.unfinished_action_intents()[0]

    recovered = supervisor.recover_intent(interrupted)
    assert recovered.value == "succeeded"
    store.record_action_outcome(interrupted.key, state=recovered)
    assert launcher.issues == [101]
    assert github.closed == [101]

    second = planner.select()
    assert second is not None
    assert second.kind is TransitionKind.START_CHILD
    assert second.issue_number == 102
    supervisor.launch(second)
    assert remote_head(repository) == initial

    publication = planner.select()
    assert publication is not None
    assert publication.kind is TransitionKind.START_INTEGRATION
    supervisor.launch(publication)

    published = remote_head(repository)
    assert published != initial
    assert git(repository, "rev-list", "--count", f"{initial}..{published}") == "2"
    assert launcher.issues == [101, 102]
    assert github.closed == [101, 102]
    assert len(github.integration_prs) == 1
    assert github.integration_prs[0].head_sha == published
    assert planner.select() is None
