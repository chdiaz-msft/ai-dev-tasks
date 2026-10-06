from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, replace

import pytest

from local_issue_runner.completion import (
    ChildCompletionService,
    CompletionBlocked,
    CompletionDelivery,
    CompletionEvidence,
    CompletionMode,
    CompletionStatus,
    LocalCommitCompletionEvidence,
    PullRequestObservation,
)
from local_issue_runner.db import CoordinatorStore
from local_issue_runner.github_api import GitHubClient
from local_issue_runner import github_api
from local_issue_runner.migrations import SCHEMA_VERSION

BASE_SHA = "a" * 40
WORK_HEAD = "b" * 40
MERGED_SHA = "c" * 40
DEPENDENT_BASE_SHA = "d" * 40
LOCAL_COMMIT = "e" * 40
SCOPE_FINGERPRINT = "scope-v1"
VALIDATION_IDENTITY = "validation:local:scope-v1"


def delivery() -> CompletionDelivery:
    return CompletionDelivery(
        repository="owner/project",
        parent_issue=100,
        issue_number=101,
        delivery=2,
        pr_number=17,
        base_ref="feature/100",
        base_commit=BASE_SHA,
        work_head=WORK_HEAD,
        scope_fingerprint=SCOPE_FINGERPRINT,
        job_succeeded=True,
    )


def merged_pr() -> PullRequestObservation:
    return PullRequestObservation(
        number=17,
        state="CLOSED",
        merged=True,
        head_sha=WORK_HEAD,
        base_ref="feature/100",
        resulting_commit=MERGED_SHA,
    )


@dataclass
class MemoryCompletionStore:
    evidence_by_delivery: dict[str, CompletionEvidence] = field(default_factory=dict)
    local_evidence_by_delivery: dict[str, LocalCommitCompletionEvidence] = field(
        default_factory=dict
    )
    acknowledgements: dict[str, str] = field(default_factory=dict)

    def completion(
        self,
        delivery_key: str,
        mode: CompletionMode = CompletionMode.PULL_REQUEST,
    ) -> CompletionEvidence | LocalCommitCompletionEvidence | None:
        if mode is CompletionMode.LOCAL_COMMIT:
            return self.local_evidence_by_delivery.get(delivery_key)
        return self.evidence_by_delivery.get(delivery_key)

    def record_completion(
        self, evidence: CompletionEvidence | LocalCommitCompletionEvidence
    ) -> None:
        if isinstance(evidence, LocalCommitCompletionEvidence):
            self.local_evidence_by_delivery[evidence.delivery.key] = evidence
        else:
            self.evidence_by_delivery[evidence.delivery.key] = evidence

    def acknowledgement(self, delivery_key: str) -> str | None:
        return self.acknowledgements.get(delivery_key)

    def record_acknowledgement(self, delivery_key: str, reason: str) -> None:
        self.acknowledgements[delivery_key] = reason


@dataclass
class RecordingGit:
    available_commits: set[tuple[str, str]] = field(
        default_factory=lambda: {
            (MERGED_SHA, "feature/100"),
            (MERGED_SHA, "feature/100-dependent"),
            (LOCAL_COMMIT, "feature/100"),
            (LOCAL_COMMIT, "feature/100-dependent"),
        }
    )
    verification_calls: list[tuple[str, str, str]] = field(default_factory=list)

    def commit_available_in_branch(
        self, repository: str, commit: str, branch: str
    ) -> bool:
        self.verification_calls.append((repository, commit, branch))
        return (commit, branch) in self.available_commits


@dataclass
class RecordingGitHub:
    pull_request: PullRequestObservation | None = field(default_factory=merged_pr)
    issue_state: str = "OPEN"
    close_failures: int = 0
    pr_reads: int = 0
    close_calls: list[tuple[str, int, str]] = field(default_factory=list)

    def get_pull_request(
        self, repository: str, number: int
    ) -> PullRequestObservation | None:
        self.pr_reads += 1
        return self.pull_request

    def get_issue_state(self, repository: str, number: int) -> str:
        return self.issue_state

    def close_issue_as_completed(
        self, repository: str, number: int, evidence_url: str
    ) -> None:
        self.close_calls.append((repository, number, evidence_url))
        if self.close_failures:
            self.close_failures -= 1
            raise RuntimeError("temporary issue closure failure")
        self.issue_state = "CLOSED"


def service(
    *,
    store: MemoryCompletionStore | None = None,
    git: RecordingGit | None = None,
    github: RecordingGitHub | None = None,
) -> tuple[
    ChildCompletionService,
    MemoryCompletionStore,
    RecordingGit,
    RecordingGitHub,
]:
    completion_store = store or MemoryCompletionStore()
    completion_git = git or RecordingGit()
    completion_github = github or RecordingGitHub()
    return (
        ChildCompletionService(
            store=completion_store,
            git=completion_git,
            github=completion_github,
        ),
        completion_store,
        completion_git,
        completion_github,
    )


def test_merged_child_records_resulting_commit_base_and_delivery_scope() -> None:
    subject, store, git, _ = service()

    result = subject.reconcile(delivery())

    assert result.status is CompletionStatus.COMPLETE
    evidence = store.evidence_by_delivery[delivery().key]
    assert evidence.resulting_commit == MERGED_SHA
    assert evidence.recorded_base == BASE_SHA
    assert evidence.base_ref == "feature/100"
    assert evidence.work_head == WORK_HEAD
    assert evidence.scope_fingerprint == SCOPE_FINGERPRINT
    assert git.verification_calls == [("owner/project", MERGED_SHA, "feature/100")]


def test_merge_into_non_default_feature_branch_explicitly_closes_issue() -> None:
    subject, _, _, github = service()

    result = subject.reconcile(delivery())

    assert result.status is CompletionStatus.COMPLETE
    assert github.close_calls == [
        (
            "owner/project",
            101,
            "https://github.com/owner/project/pull/17",
        )
    ]


def test_retry_after_closure_failure_only_retries_issue_closure() -> None:
    github = RecordingGitHub(close_failures=1)
    subject, store, git, _ = service(github=github)

    with pytest.raises(RuntimeError, match="closure"):
        subject.reconcile(delivery())

    assert store.completion(delivery().key) is not None
    assert len(git.verification_calls) == 1
    assert github.pr_reads == 1

    result = subject.reconcile(delivery())

    assert result.status is CompletionStatus.COMPLETE
    assert len(git.verification_calls) == 1
    assert github.pr_reads == 1
    assert len(github.close_calls) == 2


def test_local_commit_records_durable_evidence_then_closes_issue() -> None:
    local_delivery = replace(delivery(), pr_number=None, work_head=None)
    subject, store, git, github = service()

    result = subject.reconcile(
        local_delivery,
        mode=CompletionMode.LOCAL_COMMIT,
        resulting_commit=LOCAL_COMMIT,
        validation_identity=VALIDATION_IDENTITY,
    )

    assert result.status is CompletionStatus.COMPLETE
    evidence = store.local_evidence_by_delivery[local_delivery.key]
    assert evidence.feature_branch == "feature/100"
    assert evidence.feature_base == BASE_SHA
    assert evidence.resulting_commit == LOCAL_COMMIT
    assert evidence.scope_fingerprint == SCOPE_FINGERPRINT
    assert evidence.validation_identity == VALIDATION_IDENTITY
    assert git.verification_calls == [("owner/project", LOCAL_COMMIT, "feature/100")]
    assert github.close_calls == [
        (
            "owner/project",
            101,
            f"https://github.com/owner/project/commit/{LOCAL_COMMIT}",
        )
    ]


def test_local_commit_closure_retry_does_not_duplicate_or_reverify_evidence() -> None:
    local_delivery = replace(delivery(), pr_number=None, work_head=None)
    github = RecordingGitHub(close_failures=1)
    subject, store, git, _ = service(github=github)

    with pytest.raises(RuntimeError, match="closure"):
        subject.reconcile(
            local_delivery,
            mode=CompletionMode.LOCAL_COMMIT,
            resulting_commit=LOCAL_COMMIT,
            validation_identity=VALIDATION_IDENTITY,
        )

    result = subject.reconcile(
        local_delivery,
        mode=CompletionMode.LOCAL_COMMIT,
    )

    assert result.status is CompletionStatus.COMPLETE
    assert len(store.local_evidence_by_delivery) == 1
    assert git.verification_calls == [("owner/project", LOCAL_COMMIT, "feature/100")]
    assert len(github.close_calls) == 2


def test_local_commit_reconcile_rejects_stale_delivery_evidence() -> None:
    local_delivery = replace(delivery(), pr_number=None, work_head=None)
    subject, store, _, github = service()
    store.local_evidence_by_delivery[local_delivery.key] = (
        LocalCommitCompletionEvidence(
            delivery=local_delivery,
            feature_branch=local_delivery.base_ref,
            feature_base=local_delivery.base_commit,
            resulting_commit=LOCAL_COMMIT,
            scope_fingerprint=local_delivery.scope_fingerprint,
            validation_identity=VALIDATION_IDENTITY,
        )
    )

    with pytest.raises(CompletionBlocked, match="does not match"):
        subject.reconcile(
            replace(local_delivery, scope_fingerprint="scope-v2"),
            mode=CompletionMode.LOCAL_COMMIT,
        )

    assert github.close_calls == []


def test_local_commit_dependency_requires_evidence_and_branch_reachability() -> None:
    local_delivery = replace(delivery(), pr_number=None, work_head=None)
    github = RecordingGitHub(issue_state="CLOSED")
    subject, _, git, _ = service(github=github)

    assert (
        subject.dependency_satisfied(
            local_delivery,
            prospective_base_ref="feature/100-dependent",
            mode=CompletionMode.LOCAL_COMMIT,
        )
        is False
    )

    subject.reconcile(
        local_delivery,
        mode=CompletionMode.LOCAL_COMMIT,
        resulting_commit=LOCAL_COMMIT,
        validation_identity=VALIDATION_IDENTITY,
    )
    assert (
        subject.dependency_satisfied(
            local_delivery,
            prospective_base_ref="feature/100-dependent",
            mode=CompletionMode.LOCAL_COMMIT,
        )
        is True
    )

    git.available_commits.remove((LOCAL_COMMIT, "feature/100-dependent"))
    assert (
        subject.dependency_satisfied(
            local_delivery,
            prospective_base_ref="feature/100-dependent",
            mode=CompletionMode.LOCAL_COMMIT,
        )
        is False
    )


def test_local_completion_evidence_persists_with_all_validation_identity(
    tmp_path,
) -> None:
    local_delivery = replace(delivery(), pr_number=None, work_head=None)
    database = tmp_path / "runner.db"
    store = CoordinatorStore(database)
    evidence = LocalCommitCompletionEvidence(
        delivery=local_delivery,
        feature_branch=local_delivery.base_ref,
        feature_base=local_delivery.base_commit,
        resulting_commit=LOCAL_COMMIT,
        scope_fingerprint=local_delivery.scope_fingerprint,
        validation_identity=VALIDATION_IDENTITY,
    )

    store.record_completion(evidence)

    assert (
        CoordinatorStore(database).completion(
            local_delivery.key, CompletionMode.LOCAL_COMMIT
        )
        == evidence
    )


def test_schema_v11_keeps_local_and_pull_request_evidence_separate(
    tmp_path,
) -> None:
    database = tmp_path / "runner.db"
    CoordinatorStore(database)

    with sqlite3.connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        local_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(local_completion_evidence)"
            )
        }
        pr_columns = {
            row[1]: row[3]
            for row in connection.execute("PRAGMA table_info(completion_evidence)")
        }

    assert version == SCHEMA_VERSION == 11
    assert local_columns == {
        "delivery_key",
        "repository",
        "parent_issue",
        "issue_number",
        "delivery",
        "feature_branch",
        "feature_base",
        "resulting_commit",
        "scope_fingerprint",
        "validation_identity",
        "recorded_at",
    }
    assert pr_columns["pr_number"] == 1


def test_store_rejects_completion_evidence_for_two_delivery_modes(
    tmp_path,
) -> None:
    local_delivery = replace(delivery(), pr_number=None, work_head=None)
    store = CoordinatorStore(tmp_path / "runner.db")
    store.record_completion(
        LocalCommitCompletionEvidence(
            delivery=local_delivery,
            feature_branch=local_delivery.base_ref,
            feature_base=local_delivery.base_commit,
            resulting_commit=LOCAL_COMMIT,
            scope_fingerprint=local_delivery.scope_fingerprint,
            validation_identity=VALIDATION_IDENTITY,
        )
    )

    with pytest.raises(ValueError, match="local completion"):
        store.record_completion(
            CompletionEvidence(
                delivery=delivery(),
                pr_number=17,
                resulting_commit=MERGED_SHA,
                recorded_base=BASE_SHA,
                base_ref="feature/100",
                work_head=WORK_HEAD,
                scope_fingerprint=SCOPE_FINGERPRINT,
            )
        )


def test_github_completion_close_is_idempotent_and_repairs_missing_comment(
    monkeypatch,
) -> None:
    issue_state = "closed"
    comments: list[dict[str, object]] = []
    calls: list[tuple[str, ...]] = []

    def fake_gh_json(*args: str) -> object:
        nonlocal issue_state
        calls.append(args)
        endpoint = next(argument for argument in args if argument.startswith("repos/"))
        if endpoint.endswith("/comments?per_page=100"):
            return [list(comments)]
        if endpoint.endswith("/comments"):
            body = args[args.index("-f") + 1].removeprefix("body=")
            posted = {"id": 1, "body": body}
            comments.append(posted)
            return posted
        if "--method" in args and "PATCH" in args:
            issue_state = "closed"
        return {"number": 101, "state": issue_state}

    monkeypatch.setattr(github_api, "_gh_json", fake_gh_json)
    client = GitHubClient()
    evidence_url = f"https://github.com/owner/project/commit/{LOCAL_COMMIT}"

    client.close_issue_as_completed("owner/project", 101, evidence_url)
    client.close_issue_as_completed("owner/project", 101, evidence_url)

    post_calls = [call for call in calls if "--method" in call and "POST" in call]
    patch_calls = [call for call in calls if "--method" in call and "PATCH" in call]
    assert len(post_calls) == 1
    assert patch_calls == []
    assert evidence_url in str(comments[0]["body"])


def test_dependency_requires_resulting_commit_in_prospective_base_branch() -> None:
    subject, _, git, _ = service()
    subject.reconcile(delivery())

    assert (
        subject.dependency_satisfied(
            delivery(), prospective_base_ref="feature/100-dependent"
        )
        is True
    )

    git.available_commits.remove((MERGED_SHA, "feature/100-dependent"))

    assert (
        subject.dependency_satisfied(
            delivery(), prospective_base_ref="feature/100-dependent"
        )
        is False
    )


def test_closed_issue_without_available_completion_commit_does_not_unlock() -> None:
    github = RecordingGitHub(issue_state="CLOSED")
    subject, _, _, _ = service(github=github)

    assert (
        subject.dependency_satisfied(
            delivery(), prospective_base_ref="feature/100-dependent"
        )
        is False
    )


def test_acknowledge_records_reason_and_satisfies_legitimate_no_code_work() -> None:
    no_code = replace(
        delivery(),
        pr_number=None,
        work_head=None,
        job_succeeded=True,
    )
    github = RecordingGitHub(pull_request=None, issue_state="CLOSED")
    subject, store, _, _ = service(github=github)

    result = subject.acknowledge(
        no_code,
        reason="Requirements were satisfied by documentation already on the feature branch.",
    )

    assert result.status is CompletionStatus.ACKNOWLEDGED
    assert store.acknowledgement(no_code.key) == (
        "Requirements were satisfied by documentation already on the feature branch."
    )
    assert (
        subject.dependency_satisfied(
            no_code, prospective_base_ref="feature/100-dependent"
        )
        is True
    )


def test_acknowledgement_cannot_bypass_an_existing_pr_merge_requirement() -> None:
    subject, store, _, _ = service()

    with pytest.raises(CompletionBlocked, match="pull request"):
        subject.acknowledge(delivery(), reason="Treat this coding delivery as done.")

    assert store.acknowledgements == {}


def test_acknowledgement_reason_persists_across_store_restarts(
    tmp_path,
) -> None:
    database = tmp_path / "runner.db"
    store = CoordinatorStore(database)

    store.record_acknowledgement(delivery().key, "No repository change was required.")

    assert CoordinatorStore(database).acknowledgement(delivery().key) == (
        "No repository change was required."
    )


@pytest.mark.parametrize(
    "candidate",
    [
        replace(merged_pr(), state="OPEN", merged=False, resulting_commit=None),
        None,
    ],
)
def test_open_pr_or_successful_job_is_not_dependency_completion(
    candidate: PullRequestObservation | None,
) -> None:
    github = RecordingGitHub(pull_request=candidate)
    subject, _, _, _ = service(github=github)

    result = subject.reconcile(delivery())

    assert result.status is CompletionStatus.WAITING
    assert (
        subject.dependency_satisfied(
            delivery(), prospective_base_ref="feature/100-dependent"
        )
        is False
    )
