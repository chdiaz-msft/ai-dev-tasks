from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from local_issue_runner.cleanup import (
    ChildCleanupService,
    CleanupBlocked,
    CleanupRequest,
    CleanupStatus,
    WorktreeObservation,
)
from local_issue_runner.merge_policy import MergeReadinessDecision, MergeTarget
from local_issue_runner.qualify import (
    QualificationContext,
    QualificationLevel,
    QualificationReceipt,
    QualificationService,
)
from local_issue_runner.reconcile import (
    ChildAutomergeService,
    ChildMergeRequest,
    ChildMergeStatus,
)

HEAD = "a" * 40
MERGED = "b" * 40
ADVANCED = "c" * 40
RUNTIME = "runner-runtime-v1"
POLICY = "merge-policy-v1"
TOOLS = (("gh", "2.80.0"), ("git", "2.51.0"))


def qualification_context() -> QualificationContext:
    return QualificationContext(
        runtime_identity=RUNTIME,
        tool_versions=TOOLS,
        policy_fingerprint=POLICY,
    )


def child_receipt() -> QualificationReceipt:
    return QualificationReceipt(
        level=QualificationLevel.CHILD,
        disposable_repository="owner/disposable",
        exercise_id="exercise-7",
        successful=True,
        observed_transitions=("child_merge", "restart_recovery"),
        context=qualification_context(),
        completed_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
        evidence_paths=("evidence/merge.json", "evidence/restart.json"),
    )


def merge_request() -> ChildMergeRequest:
    return ChildMergeRequest(
        repository="owner/project",
        parent_issue=100,
        issue_number=101,
        delivery=2,
        pr_number=17,
        head_ref="runner/p-100/i-101/d-2",
        expected_head=HEAD,
        base_ref="feature/100",
    )


def eligible() -> MergeReadinessDecision:
    return MergeReadinessDecision(MergeTarget.CHILD, ())


@dataclass
class MemoryMergeStore:
    owned: bool = True
    merged_commit: str | None = None
    merge_intent: ChildMergeRequest | None = None

    def runner_owns_pull_request(self, request: ChildMergeRequest) -> bool:
        return self.owned

    def record_merge_intent(self, request: ChildMergeRequest) -> None:
        self.merge_intent = request

    def record_merge_result(
        self, request: ChildMergeRequest, resulting_commit: str
    ) -> None:
        self.merged_commit = resulting_commit

    def recorded_merge(self, request: ChildMergeRequest) -> str | None:
        return self.merged_commit


@dataclass
class FakeMergeGitHub:
    merged: bool = False
    head: str = HEAD
    resulting_commit: str = MERGED
    merge_calls: list[tuple[int, str, str]] = field(default_factory=list)

    def observe_child_merge(
        self, repository: str, pr_number: int
    ) -> tuple[bool, str, str | None]:
        return self.merged, self.head, self.resulting_commit if self.merged else None

    def squash_merge_child(
        self, repository: str, pr_number: int, expected_head: str
    ) -> str:
        self.merge_calls.append((pr_number, expected_head, "squash"))
        if self.head != expected_head:
            raise RuntimeError("expected head does not match")
        self.merged = True
        return self.resulting_commit


@dataclass
class RecordingCompletion:
    closes: list[ChildMergeRequest] = field(default_factory=list)

    def close_verified_child(self, request: ChildMergeRequest) -> None:
        self.closes.append(request)


def automerge(
    *,
    store: MemoryMergeStore | None = None,
    github: FakeMergeGitHub | None = None,
    completion: RecordingCompletion | None = None,
    receipt: QualificationReceipt | None = None,
) -> tuple[
    ChildAutomergeService,
    MemoryMergeStore,
    FakeMergeGitHub,
    RecordingCompletion,
]:
    merge_store = store or MemoryMergeStore()
    merge_github = github or FakeMergeGitHub()
    merge_completion = completion or RecordingCompletion()
    service = ChildAutomergeService(
        store=merge_store,
        github=merge_github,
        completion=merge_completion,
        qualification=QualificationService(
            receipt=receipt, current=qualification_context()
        ),
    )
    return service, merge_store, merge_github, merge_completion


@pytest.mark.parametrize(
    "receipt",
    [
        None,
        replace(child_receipt(), successful=False),
        replace(
            child_receipt(),
            observed_transitions=("child_merge",),
        ),
        replace(
            child_receipt(),
            context=replace(
                qualification_context(), runtime_identity="runner-runtime-v2"
            ),
        ),
    ],
)
def test_merge_children_is_unavailable_without_valid_child_qualification(
    receipt: QualificationReceipt | None,
) -> None:
    subject, store, github, completion = automerge(receipt=receipt)

    result = subject.reconcile(merge_request(), readiness=eligible())

    assert result.status is ChildMergeStatus.BLOCKED_QUALIFICATION
    assert store.merge_intent is None
    assert github.merge_calls == []
    assert completion.closes == []


@pytest.mark.parametrize("runner_owned", [True, False])
def test_expected_head_squash_merge_is_limited_to_runner_owned_prs(
    runner_owned: bool,
) -> None:
    subject, store, github, completion = automerge(
        store=MemoryMergeStore(owned=runner_owned),
        receipt=child_receipt(),
    )

    result = subject.reconcile(merge_request(), readiness=eligible())

    if runner_owned:
        assert result.status is ChildMergeStatus.MERGED
        assert store.merge_intent == merge_request()
        assert store.merged_commit == MERGED
        assert github.merge_calls == [(17, HEAD, "squash")]
        assert completion.closes == [merge_request()]
    else:
        assert result.status is ChildMergeStatus.BLOCKED_OWNERSHIP
        assert store.merge_intent is None
        assert github.merge_calls == []
        assert completion.closes == []


def test_restart_after_remote_merge_closes_child_without_second_merge() -> None:
    store = MemoryMergeStore(merge_intent=merge_request())
    github = FakeMergeGitHub(merged=True)
    completion = RecordingCompletion()

    restarted, _, _, _ = automerge(
        store=store,
        github=github,
        completion=completion,
        receipt=child_receipt(),
    )
    result = restarted.reconcile(merge_request(), readiness=eligible())

    assert result.status is ChildMergeStatus.COMPLETED
    assert store.merged_commit == MERGED
    assert github.merge_calls == []
    assert completion.closes == [merge_request()]


def cleanup_request(tmp_path: Path) -> CleanupRequest:
    return CleanupRequest(
        repository=tmp_path / "repo",
        worktree=tmp_path / "runtime" / "worktrees" / "p-100-i-101-d-2",
        ownership_key="owner/project:100:101:2",
        local_ref="refs/heads/runner/p-100/i-101/d-2",
        remote="origin",
        remote_ref="refs/heads/runner/p-100/i-101/d-2",
        expected_tip=HEAD,
    )


@dataclass
class MemoryCleanupStore:
    owned: bool = True
    recorded: CleanupRequest | None = None
    complete: bool = False

    def record_cleanup_intent(self, request: CleanupRequest) -> None:
        if self.recorded is not None and self.recorded != request:
            raise AssertionError("cleanup recovery changed the recorded targets")
        self.recorded = request

    def cleanup_request(self, ownership_key: str) -> CleanupRequest | None:
        return self.recorded

    def ownership_is_active(self, ownership_key: str) -> bool:
        return self.owned

    def record_cleanup_complete(self, ownership_key: str) -> None:
        self.complete = True
        self.owned = False


@dataclass
class FakeCleanupGit:
    observation: WorktreeObservation
    local_tip: str | None = HEAD
    remote_tip: str | None = HEAD
    fail_after_worktree_remove: bool = False
    removed_worktrees: list[Path] = field(default_factory=list)
    deleted_local_refs: list[tuple[str, str]] = field(default_factory=list)
    deleted_remote_refs: list[tuple[str, str, str]] = field(default_factory=list)

    def observe_worktree(self, path: Path) -> WorktreeObservation:
        if path in self.removed_worktrees:
            return replace(self.observation, exists=False)
        return self.observation

    def remove_worktree(self, repository: Path, path: Path) -> None:
        self.removed_worktrees.append(path)
        if self.fail_after_worktree_remove:
            self.fail_after_worktree_remove = False
            raise RuntimeError("simulated crash")

    def ref_tip(self, repository: Path, ref: str) -> str | None:
        return self.local_tip

    def delete_local_ref(self, repository: Path, ref: str, expected_tip: str) -> None:
        self.deleted_local_refs.append((ref, expected_tip))
        self.local_tip = None

    def remote_ref_tip(
        self, repository: Path, remote: str, ref: str
    ) -> str | None:
        return self.remote_tip

    def delete_remote_ref(
        self, repository: Path, remote: str, ref: str, expected_tip: str
    ) -> None:
        self.deleted_remote_refs.append((remote, ref, expected_tip))
        self.remote_tip = None


def clean_observation(request: CleanupRequest) -> WorktreeObservation:
    return WorktreeObservation(
        exists=True,
        runner_owned=True,
        ownership_key=request.ownership_key,
        branch_ref=request.local_ref,
        head=HEAD,
        dirty=False,
        in_use=False,
    )


def test_cleanup_deletes_only_verified_owned_worktree_and_unchanged_refs(
    tmp_path: Path,
) -> None:
    request = cleanup_request(tmp_path)
    store = MemoryCleanupStore()
    git = FakeCleanupGit(clean_observation(request))

    result = ChildCleanupService(store=store, git=git).reconcile(request)

    assert result.status is CleanupStatus.COMPLETE
    assert git.removed_worktrees == [request.worktree]
    assert git.deleted_local_refs == [(request.local_ref, HEAD)]
    assert git.deleted_remote_refs == [("origin", request.remote_ref, HEAD)]
    assert store.complete is True


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"dirty": True}, "dirty"),
        ({"in_use": True}, "in use"),
        ({"runner_owned": False}, "owned"),
        ({"ownership_key": "another-delivery"}, "ownership"),
    ],
)
def test_unsafe_worktree_blocks_cleanup_without_deleting_anything(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    request = cleanup_request(tmp_path)
    git = FakeCleanupGit(replace(clean_observation(request), **changes))

    with pytest.raises(CleanupBlocked, match=message):
        ChildCleanupService(store=MemoryCleanupStore(), git=git).reconcile(request)

    assert git.removed_worktrees == []
    assert git.deleted_local_refs == []
    assert git.deleted_remote_refs == []


@pytest.mark.parametrize("actual_tip", [ADVANCED, "d" * 40])
def test_advanced_or_rewritten_remote_ref_blocks_remote_deletion(
    tmp_path: Path, actual_tip: str
) -> None:
    request = cleanup_request(tmp_path)
    git = FakeCleanupGit(
        clean_observation(request),
        remote_tip=actual_tip,
    )

    with pytest.raises(CleanupBlocked, match="remote.*tip"):
        ChildCleanupService(store=MemoryCleanupStore(), git=git).reconcile(request)

    assert git.removed_worktrees == []
    assert git.deleted_local_refs == []
    assert git.deleted_remote_refs == []
    assert git.remote_tip == actual_tip


def test_cleanup_restart_uses_exact_recorded_targets_and_expected_tips(
    tmp_path: Path,
) -> None:
    request = cleanup_request(tmp_path)
    store = MemoryCleanupStore()
    git = FakeCleanupGit(
        clean_observation(request),
        fail_after_worktree_remove=True,
    )
    subject = ChildCleanupService(store=store, git=git)

    with pytest.raises(RuntimeError, match="simulated crash"):
        subject.reconcile(request)

    assert store.recorded == request
    recovered = ChildCleanupService(store=store, git=git).recover(
        request.ownership_key
    )

    assert recovered.status is CleanupStatus.COMPLETE
    assert git.removed_worktrees == [request.worktree]
    assert git.deleted_local_refs == [(request.local_ref, request.expected_tip)]
    assert git.deleted_remote_refs == [
        (request.remote, request.remote_ref, request.expected_tip)
    ]
    assert store.complete is True
