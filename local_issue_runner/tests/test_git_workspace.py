from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from local_issue_runner.git_ops import (
    FeatureBranchWorkspace,
    GitOperations,
    PublicationState,
    RemoteDivergence,
    WorktreeCollision,
)


def git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(path), *args),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def commit(path: Path, name: str, content: str) -> str:
    (path / name).write_text(content, encoding="utf-8")
    git(path, "add", name)
    git(path, "commit", "-m", f"update {name}")
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Path, Path, str]:
    remote = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"
    subprocess.run(("git", "init", "--bare", str(remote)), check=True)
    subprocess.run(("git", "clone", str(remote), str(checkout)), check=True)
    git(checkout, "config", "user.name", "Runner Tests")
    git(checkout, "config", "user.email", "runner@example.test")
    git(checkout, "checkout", "-b", "feature/100")
    initial = commit(checkout, "feature.txt", "initial\n")
    git(checkout, "push", "-u", "origin", "feature/100")
    git(checkout, "checkout", "--detach")
    return checkout, remote, initial


def test_feature_workspace_is_deterministic_and_preserves_unpublished_commit(
    tmp_path: Path, repository: tuple[Path, Path, str]
) -> None:
    checkout, _, remote_head = repository
    developer_head = git(checkout, "rev-parse", "HEAD")
    developer_status = git(checkout, "status", "--porcelain=v1")
    workspace = FeatureBranchWorkspace(tmp_path / "runtime")

    first = workspace.prepare(
        repository=checkout,
        remote="origin",
        feature_branch="feature/100",
        parent_issue=100,
        expected_remote_head=remote_head,
    )
    unpublished = commit(first.worktree, "unpublished.txt", "keep me\n")
    restarted = workspace.prepare(
        repository=checkout,
        remote="origin",
        feature_branch="feature/100",
        parent_issue=100,
        expected_remote_head=remote_head,
    )

    assert restarted == first.__class__(
        first.worktree, "feature/100", remote_head, unpublished
    )
    assert (restarted.worktree / "unpublished.txt").read_text(
        encoding="utf-8"
    ) == "keep me\n"
    assert git(checkout, "rev-parse", "HEAD") == developer_head
    assert git(checkout, "status", "--porcelain=v1") == developer_status


def test_feature_workspace_rejects_dirty_or_unowned_collisions(
    tmp_path: Path, repository: tuple[Path, Path, str]
) -> None:
    checkout, _, remote_head = repository
    workspace = FeatureBranchWorkspace(tmp_path / "runtime")
    prepared = workspace.prepare(
        repository=checkout,
        remote="origin",
        feature_branch="feature/100",
        parent_issue=100,
        expected_remote_head=remote_head,
    )
    (prepared.worktree / "feature.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(WorktreeCollision, match="dirty"):
        workspace.prepare(
            repository=checkout,
            remote="origin",
            feature_branch="feature/100",
            parent_issue=100,
            expected_remote_head=remote_head,
        )

    other = FeatureBranchWorkspace(tmp_path / "other-runtime")
    with pytest.raises(WorktreeCollision, match="branch"):
        other.prepare(
            repository=checkout,
            remote="origin",
            feature_branch="feature/100",
            parent_issue=100,
            expected_remote_head=remote_head,
        )


def test_feature_workspace_rejects_unpublished_local_branch_divergence(
    tmp_path: Path, repository: tuple[Path, Path, str]
) -> None:
    checkout, _, remote_head = repository
    git(checkout, "checkout", "-b", "local-work", remote_head)
    unpublished = commit(checkout, "local-only.txt", "do not discard\n")
    git(checkout, "checkout", "--detach", remote_head)
    git(checkout, "branch", "-f", "feature/100", unpublished)

    with pytest.raises(WorktreeCollision, match="refusing to discard"):
        FeatureBranchWorkspace(tmp_path / "runtime").prepare(
            repository=checkout,
            remote="origin",
            feature_branch="feature/100",
            parent_issue=100,
            expected_remote_head=remote_head,
        )

    assert git(checkout, "rev-parse", "feature/100") == unpublished
    assert git(checkout, "rev-parse", "HEAD") == remote_head


def test_feature_workspace_restart_rejects_remote_divergence(
    tmp_path: Path, repository: tuple[Path, Path, str]
) -> None:
    checkout, remote, remote_head = repository
    workspace = FeatureBranchWorkspace(tmp_path / "runtime")
    prepared = workspace.prepare(
        repository=checkout,
        remote="origin",
        feature_branch="feature/100",
        parent_issue=100,
        expected_remote_head=remote_head,
    )
    commit(prepared.worktree, "unpublished.txt", "local\n")

    other = tmp_path / "other"
    subprocess.run(("git", "clone", str(remote), str(other)), check=True)
    git(other, "config", "user.name", "Other")
    git(other, "config", "user.email", "other@example.test")
    git(other, "checkout", "feature/100")
    divergent = commit(other, "external.txt", "external\n")
    git(other, "push", "origin", "feature/100")

    with pytest.raises(RemoteDivergence, match=divergent):
        workspace.prepare(
            repository=checkout,
            remote="origin",
            feature_branch="feature/100",
            parent_issue=100,
            expected_remote_head=remote_head,
        )


def test_commit_helpers_require_one_direct_child(
    tmp_path: Path, repository: tuple[Path, Path, str]
) -> None:
    checkout, _, remote_head = repository
    prepared = FeatureBranchWorkspace(tmp_path / "runtime").prepare(
        repository=checkout,
        remote="origin",
        feature_branch="feature/100",
        parent_issue=100,
        expected_remote_head=remote_head,
    )
    child = commit(prepared.worktree, "child.txt", "child\n")
    operations = GitOperations()

    assert operations.is_ancestor(prepared.worktree, remote_head, child)
    assert operations.commit_count(prepared.worktree, remote_head, child) == 1
    assert (
        operations.require_single_direct_child(
            prepared.worktree, remote_head, child
        )
        == child
    )

    grandchild = commit(prepared.worktree, "grandchild.txt", "grandchild\n")
    with pytest.raises(WorktreeCollision, match="exactly one"):
        operations.require_single_direct_child(
            prepared.worktree, remote_head, grandchild
        )


def test_commit_helpers_reject_result_mismatch_and_dirty_tracked_state(
    tmp_path: Path, repository: tuple[Path, Path, str]
) -> None:
    checkout, _, remote_head = repository
    prepared = FeatureBranchWorkspace(tmp_path / "runtime").prepare(
        repository=checkout,
        remote="origin",
        feature_branch="feature/100",
        parent_issue=100,
        expected_remote_head=remote_head,
    )
    child = commit(prepared.worktree, "child.txt", "child\n")
    operations = GitOperations()

    with pytest.raises(WorktreeCollision, match="does not match worktree HEAD"):
        operations.require_single_direct_child(
            prepared.worktree, remote_head, remote_head
        )

    (prepared.worktree / "child.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(WorktreeCollision, match="dirty tracked state"):
        operations.require_single_direct_child(
            prepared.worktree, remote_head, child
        )


def test_commit_helpers_reject_untracked_delivery_output(
    tmp_path: Path, repository: tuple[Path, Path, str]
) -> None:
    checkout, _, remote_head = repository
    prepared = FeatureBranchWorkspace(tmp_path / "runtime").prepare(
        repository=checkout,
        remote="origin",
        feature_branch="feature/100",
        parent_issue=100,
        expected_remote_head=remote_head,
    )
    child = commit(prepared.worktree, "child.txt", "child\n")
    (prepared.worktree / "forgotten.txt").write_text("uncommitted\n", encoding="utf-8")

    with pytest.raises(WorktreeCollision, match="dirty tracked state"):
        GitOperations().require_single_direct_child(
            prepared.worktree, remote_head, child
        )


def test_owned_reset_cannot_target_an_unowned_worktree(
    tmp_path: Path, repository: tuple[Path, Path, str]
) -> None:
    checkout, _, remote_head = repository
    workspace = FeatureBranchWorkspace(tmp_path / "runtime")
    prepared = workspace.prepare(
        repository=checkout,
        remote="origin",
        feature_branch="feature/100",
        parent_issue=100,
        expected_remote_head=remote_head,
    )
    commit(prepared.worktree, "throw-away.txt", "discard\n")
    (prepared.worktree / "untracked.txt").write_text("remove\n", encoding="utf-8")

    workspace.reset(
        parent_issue=100,
        feature_branch="feature/100",
        target_head=remote_head,
    )

    assert git(prepared.worktree, "rev-parse", "HEAD") == remote_head
    assert not (prepared.worktree / "untracked.txt").exists()
    with pytest.raises(WorktreeCollision, match="rollback target"):
        workspace.reset(
            parent_issue=100,
            feature_branch="feature/100",
            target_head="f" * 40,
        )
    with pytest.raises(WorktreeCollision, match="ownership"):
        workspace.reset(
            parent_issue=999,
            feature_branch="feature/100",
            target_head=remote_head,
        )


def test_owned_reset_preserves_previous_unpublished_child(
    tmp_path: Path, repository: tuple[Path, Path, str]
) -> None:
    checkout, _, remote_head = repository
    workspace = FeatureBranchWorkspace(tmp_path / "runtime")
    prepared = workspace.prepare(
        repository=checkout,
        remote="origin",
        feature_branch="feature/100",
        parent_issue=100,
        expected_remote_head=remote_head,
    )
    first_child = commit(prepared.worktree, "first.txt", "first\n")
    commit(prepared.worktree, "second.txt", "second\n")

    workspace.reset(
        parent_issue=100,
        feature_branch="feature/100",
        target_head=first_child,
    )

    assert git(prepared.worktree, "rev-parse", "HEAD") == first_child
    assert (prepared.worktree / "first.txt").exists()
    assert not (prepared.worktree / "second.txt").exists()


def test_published_head_becomes_next_batch_base(
    tmp_path: Path, repository: tuple[Path, Path, str]
) -> None:
    checkout, _, remote_head = repository
    workspace = FeatureBranchWorkspace(tmp_path / "runtime")
    prepared = workspace.prepare(
        repository=checkout,
        remote="origin",
        feature_branch="feature/100",
        parent_issue=100,
        expected_remote_head=remote_head,
    )
    published = commit(prepared.worktree, "published.txt", "published\n")
    git(prepared.worktree, "push", "origin", "feature/100")

    next_batch = workspace.prepare(
        repository=checkout,
        remote="origin",
        feature_branch="feature/100",
        parent_issue=100,
        expected_remote_head=published,
    )

    assert next_batch.base_head == published
    assert next_batch.head == published


def test_compare_and_swap_push_recovers_and_blocks_remote_divergence(
    tmp_path: Path, repository: tuple[Path, Path, str]
) -> None:
    checkout, remote, remote_head = repository
    prepared = FeatureBranchWorkspace(tmp_path / "runtime").prepare(
        repository=checkout,
        remote="origin",
        feature_branch="feature/100",
        parent_issue=100,
        expected_remote_head=remote_head,
    )
    local_head = commit(prepared.worktree, "publish.txt", "publish\n")
    operations = GitOperations()

    assert operations.publish_branch(
        prepared.worktree,
        "origin",
        "feature/100",
        expected_remote_head=remote_head,
        new_head=local_head,
    ) is PublicationState.PUBLISHED
    assert operations.publish_branch(
        prepared.worktree,
        "origin",
        "feature/100",
        expected_remote_head=remote_head,
        new_head=local_head,
    ) is PublicationState.ALREADY_PUBLISHED

    other = tmp_path / "other"
    subprocess.run(("git", "clone", str(remote), str(other)), check=True)
    git(other, "config", "user.name", "Other")
    git(other, "config", "user.email", "other@example.test")
    git(other, "checkout", "feature/100")
    divergent = commit(other, "external.txt", "external\n")
    git(other, "push", "origin", "feature/100")

    with pytest.raises(RemoteDivergence, match=divergent):
        operations.publish_branch(
            prepared.worktree,
            "origin",
            "feature/100",
            expected_remote_head=remote_head,
            new_head=local_head,
        )
