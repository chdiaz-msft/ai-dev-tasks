from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from local_issue_runner.db import ChildPRStore
from local_issue_runner.github_api import (
    ChildPullRequest,
    child_pr_ownership_marker,
)
from local_issue_runner.models import (
    ActionState,
    ChildPRIdentity,
    ChildPRRequest,
    ChildPRState,
)
from local_issue_runner.reconcile import ChildPRPublisher, PublicationBlocked

HEAD_SHA = "a" * 40


def identity() -> ChildPRIdentity:
    return ChildPRIdentity(
        repository="owner/project",
        parent_issue=100,
        issue_number=101,
        delivery=2,
        work_id="work-7",
    )


def request() -> ChildPRRequest:
    return ChildPRRequest(
        identity=identity(),
        head_ref="runner/p-100/i-101/d-2",
        head_sha=HEAD_SHA,
        base_ref="feature/100",
        title="Implement child issue 101",
        body="Implements #101.",
    )


def pull_request(
    *,
    number: int = 17,
    state: str = "OPEN",
    head_ref: str = "runner/p-100/i-101/d-2",
    head_sha: str = HEAD_SHA,
    base_ref: str = "feature/100",
    body: str | None = None,
    linked_issue: int = 101,
    merged: bool = False,
) -> ChildPullRequest:
    return ChildPullRequest(
        number=number,
        state=state,
        head_ref=head_ref,
        head_sha=head_sha,
        base_ref=base_ref,
        body=body if body is not None else child_pr_ownership_marker(identity()),
        linked_issue=linked_issue,
        merged=merged,
    )


@dataclass
class FakeGit:
    remote_head: str | None = HEAD_SHA
    pushes: list[tuple[str, str, str]] = field(default_factory=list)

    def remote_branch_head(
        self, repository: str, remote: str, branch: str
    ) -> str | None:
        return self.remote_head

    def push_branch(
        self, repository: str, remote: str, branch: str, expected_head: str
    ) -> None:
        self.pushes.append((remote, branch, expected_head))
        self.remote_head = expected_head


@dataclass
class FakeGitHub:
    pull_requests: list[ChildPullRequest] = field(default_factory=list)
    creates: list[ChildPRRequest] = field(default_factory=list)

    def list_child_pull_requests(
        self, repository: str, issue_number: int
    ) -> tuple[ChildPullRequest, ...]:
        return tuple(
            candidate
            for candidate in self.pull_requests
            if candidate.linked_issue == issue_number
        )

    def create_child_pull_request(self, candidate: ChildPRRequest) -> ChildPullRequest:
        self.creates.append(candidate)
        created = pull_request(number=50, body=candidate.body)
        self.pull_requests.append(created)
        return created


def publisher(
    tmp_path: Path,
    *,
    git: FakeGit | None = None,
    github: FakeGitHub | None = None,
) -> tuple[ChildPRPublisher, ChildPRStore, FakeGit, FakeGitHub]:
    store = ChildPRStore(tmp_path / "runner.db")
    fake_git = git or FakeGit()
    fake_github = github or FakeGitHub()
    return (
        ChildPRPublisher(
            store=store,
            git=fake_git,
            github=fake_github,
            remote="origin",
        ),
        store,
        fake_git,
        fake_github,
    )


def test_pr_ownership_marker_is_deterministic_and_identity_complete() -> None:
    marker = child_pr_ownership_marker(identity())

    assert marker == child_pr_ownership_marker(identity())
    assert marker.startswith("<!-- local-issue-runner:")
    assert "owner/project" in marker
    assert '"parent_issue":100' in marker
    assert '"issue_number":101' in marker
    assert '"delivery":2' in marker
    assert '"work_id":"work-7"' in marker


def test_pr_ownership_requires_an_explicit_runner_creation_record(
    tmp_path: Path,
) -> None:
    store = ChildPRStore(tmp_path / "runner.db")
    marker = child_pr_ownership_marker(identity())
    candidate = pull_request(body=marker)

    assert store.runner_created_pr(identity(), candidate) is False

    store.record_runner_pr_creation(
        identity=identity(),
        intent_key="owner/project:create-pr:work-7",
        pr_number=17,
        head_ref=candidate.head_ref,
        head_sha=candidate.head_sha,
        base_ref=candidate.base_ref,
        marker=marker,
    )

    assert store.runner_created_pr(identity(), candidate) is True


@pytest.mark.parametrize(
    ("remote_head", "candidate", "message"),
    [
        ("b" * 40, None, "remote head"),
        (HEAD_SHA, pull_request(head_ref="someone/else"), "head"),
        (HEAD_SHA, pull_request(base_ref="main"), "base"),
    ],
)
def test_exact_head_and_base_must_match_before_pr_creation(
    tmp_path: Path,
    remote_head: str,
    candidate: ChildPullRequest | None,
    message: str,
) -> None:
    github = FakeGitHub([] if candidate is None else [candidate])
    subject, _, _, _ = publisher(
        tmp_path, git=FakeGit(remote_head=remote_head), github=github
    )

    with pytest.raises(PublicationBlocked, match=message):
        subject.publish(request())

    assert github.creates == []


def test_push_recovery_observes_expected_remote_head_without_repush(
    tmp_path: Path,
) -> None:
    subject, store, git, _ = publisher(tmp_path)
    push_key = subject.push_intent_key(request())
    store.record_push_intent(request(), push_key)
    store.mark_action_running(push_key)

    result = subject.reconcile(request())

    assert result.state is ChildPRState.READY_TO_CREATE
    assert store.action_state(push_key) is ActionState.SUCCEEDED
    assert git.pushes == []


def test_pr_creation_recovery_after_crash_records_remote_pr_without_duplicate(
    tmp_path: Path,
) -> None:
    existing = pull_request()
    subject, store, _, github = publisher(
        tmp_path, github=FakeGitHub([existing])
    )
    create_key = subject.creation_intent_key(request())
    store.record_pr_creation_intent(request(), create_key)
    store.mark_action_running(create_key)

    result = subject.reconcile(request())

    assert result.state is ChildPRState.OPEN
    assert result.pr_number == 17
    assert store.action_state(create_key) is ActionState.SUCCEEDED
    assert store.runner_created_pr(identity(), existing) is True
    assert github.creates == []


def test_existing_runner_owned_pr_is_discovered_without_duplication(
    tmp_path: Path,
) -> None:
    existing = pull_request()
    subject, store, _, github = publisher(
        tmp_path, github=FakeGitHub([existing])
    )
    store.record_runner_pr_creation(
        identity=identity(),
        intent_key=subject.creation_intent_key(request()),
        pr_number=17,
        head_ref=existing.head_ref,
        head_sha=existing.head_sha,
        base_ref=existing.base_ref,
        marker=existing.body,
    )

    result = subject.publish(request())

    assert result.pr_number == 17
    assert result.state is ChildPRState.OPEN
    assert github.creates == []


def test_external_open_pr_blocks_duplicate_work_without_being_adopted(
    tmp_path: Path,
) -> None:
    external = replace(pull_request(number=23), body="Implements #101.")
    subject, store, _, github = publisher(
        tmp_path, github=FakeGitHub([external])
    )

    result = subject.reconcile(request())

    assert result.state is ChildPRState.BLOCKED_EXTERNAL
    assert result.blocked is True
    assert result.pr_number == 23
    assert "waiting for external resolution" in (result.detail or "")
    assert store.runner_created_pr(identity(), external) is False
    assert github.creates == []


def test_multiple_plausible_prs_block_without_adoption_or_publication(
    tmp_path: Path,
) -> None:
    first = replace(pull_request(number=23), body="Implements #101.")
    second = replace(pull_request(number=24), body="Also implements #101.")
    subject, store, git, github = publisher(
        tmp_path, github=FakeGitHub([first, second])
    )

    result = subject.publish(request())

    assert result.state is ChildPRState.BLOCKED_AMBIGUOUS
    assert result.blocked is True
    assert result.pr_number is None
    assert "#23, #24" in (result.detail or "")
    assert store.runner_created_pr(identity(), first) is False
    assert store.runner_created_pr(identity(), second) is False
    assert git.pushes == []
    assert github.creates == []


def test_manually_closed_runner_pr_stays_abandoned_until_explicit_retry(
    tmp_path: Path,
) -> None:
    closed = pull_request(state="CLOSED")
    subject, store, _, github = publisher(
        tmp_path, github=FakeGitHub([closed])
    )
    store.record_runner_pr_creation(
        identity=identity(),
        intent_key=subject.creation_intent_key(request()),
        pr_number=17,
        head_ref=closed.head_ref,
        head_sha=closed.head_sha,
        base_ref=closed.base_ref,
        marker=closed.body,
    )

    first = subject.reconcile(request())
    second = subject.reconcile(request())

    assert first.state is ChildPRState.ABANDONED
    assert first.blocked is True
    assert "explicit retry or a new delivery" in (first.detail or "")
    assert second.state is ChildPRState.ABANDONED
    assert "explicit retry or a new delivery" in (second.detail or "")
    assert github.creates == []

    subject.retry(identity())
    resumed = subject.reconcile(request())

    assert resumed.state is ChildPRState.READY_TO_CREATE
    assert store.abandonment(identity()) is None
