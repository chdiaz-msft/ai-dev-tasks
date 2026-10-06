from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class CleanupBlocked(RuntimeError):
    """Owned delivery artifacts cannot be proved safe to delete."""


class CleanupStatus(StrEnum):
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class CleanupRequest:
    repository: Path
    worktree: Path
    ownership_key: str
    local_ref: str
    remote: str
    remote_ref: str
    expected_tip: str

    def __post_init__(self) -> None:
        if not self.ownership_key or not self.remote or not self.expected_tip:
            raise ValueError("cleanup ownership, remote, and expected tip are required")
        if not self.local_ref.startswith("refs/heads/"):
            raise ValueError("cleanup local ref must be a branch ref")
        if not self.remote_ref.startswith("refs/heads/"):
            raise ValueError("cleanup remote ref must be a branch ref")


@dataclass(frozen=True, slots=True)
class WorktreeObservation:
    exists: bool
    runner_owned: bool
    ownership_key: str | None
    branch_ref: str | None
    head: str | None
    dirty: bool
    in_use: bool


@dataclass(frozen=True, slots=True)
class CleanupResult:
    status: CleanupStatus


@dataclass(frozen=True, slots=True)
class FeatureRetentionRequest:
    feature_branch: Path
    audit_paths: tuple[Path, ...]


def verify_feature_retention(request: FeatureRetentionRequest) -> None:
    """Finalization is retention-only: prove evidence exists and delete nothing."""
    paths = (request.feature_branch, *request.audit_paths)
    if len(paths) != len(set(paths)):
        raise CleanupBlocked("retained feature artifacts must be unique")
    missing = tuple(path for path in paths if not path.exists())
    if missing:
        raise CleanupBlocked(
            "retained feature artifacts are missing: "
            + ", ".join(str(path) for path in missing)
        )


class CleanupStore(Protocol):
    def record_cleanup_intent(self, request: CleanupRequest) -> None: ...
    def cleanup_request(self, ownership_key: str) -> CleanupRequest | None: ...
    def ownership_is_active(self, ownership_key: str) -> bool: ...
    def record_cleanup_complete(self, ownership_key: str) -> None: ...


class CleanupGit(Protocol):
    def observe_worktree(self, path: Path) -> WorktreeObservation: ...
    def remove_worktree(self, repository: Path, path: Path) -> None: ...
    def ref_tip(self, repository: Path, ref: str) -> str | None: ...
    def delete_local_ref(
        self, repository: Path, ref: str, expected_tip: str
    ) -> None: ...
    def remote_ref_tip(
        self, repository: Path, remote: str, ref: str
    ) -> str | None: ...
    def delete_remote_ref(
        self, repository: Path, remote: str, ref: str, expected_tip: str
    ) -> None: ...


class ChildCleanupService:
    def __init__(self, *, store: CleanupStore, git: CleanupGit) -> None:
        self.store = store
        self.git = git

    def recover(self, ownership_key: str) -> CleanupResult:
        request = self.store.cleanup_request(ownership_key)
        if request is None:
            raise CleanupBlocked("no recorded cleanup intent exists for this ownership")
        return self._reconcile_recorded(request)

    def reconcile(self, request: CleanupRequest) -> CleanupResult:
        recorded = self.store.cleanup_request(request.ownership_key)
        if recorded is not None and recorded != request:
            raise CleanupBlocked("cleanup targets differ from the recorded intent")
        if not self.store.ownership_is_active(request.ownership_key):
            if recorded == request:
                return CleanupResult(CleanupStatus.COMPLETE)
            raise CleanupBlocked("delivery ownership is not active")
        self.store.record_cleanup_intent(request)
        return self._reconcile_recorded(request)

    def _reconcile_recorded(self, request: CleanupRequest) -> CleanupResult:
        observation = self.git.observe_worktree(request.worktree)
        self._validate_worktree(request, observation)

        local_tip = self.git.ref_tip(request.repository, request.local_ref)
        remote_tip = self.git.remote_ref_tip(
            request.repository, request.remote, request.remote_ref
        )
        self._validate_ref_tip("local", local_tip, request.expected_tip)
        self._validate_ref_tip("remote", remote_tip, request.expected_tip)

        self._delete_recorded_artifacts(
            request,
            worktree_exists=observation.exists,
            local_tip=local_tip,
            remote_tip=remote_tip,
        )
        self.store.record_cleanup_complete(request.ownership_key)
        return CleanupResult(CleanupStatus.COMPLETE)

    @staticmethod
    def _validate_worktree(
        request: CleanupRequest, observation: WorktreeObservation
    ) -> None:
        if not observation.exists:
            return
        if not observation.runner_owned:
            raise CleanupBlocked("worktree is not runner-owned")
        if observation.ownership_key != request.ownership_key:
            raise CleanupBlocked("worktree ownership does not match cleanup intent")
        if observation.branch_ref != request.local_ref:
            raise CleanupBlocked("worktree branch does not match cleanup intent")
        if observation.head != request.expected_tip:
            raise CleanupBlocked("worktree head does not match expected tip")
        if observation.dirty:
            raise CleanupBlocked("worktree is dirty")
        if observation.in_use:
            raise CleanupBlocked("worktree is still in use")

    @staticmethod
    def _validate_ref_tip(
        kind: str, observed_tip: str | None, expected_tip: str
    ) -> None:
        if observed_tip not in {None, expected_tip}:
            raise CleanupBlocked(f"{kind} ref tip does not match expected tip")

    def _delete_recorded_artifacts(
        self,
        request: CleanupRequest,
        *,
        worktree_exists: bool,
        local_tip: str | None,
        remote_tip: str | None,
    ) -> None:
        if worktree_exists:
            self.git.remove_worktree(request.repository, request.worktree)
        if local_tip is not None:
            self.git.delete_local_ref(
                request.repository, request.local_ref, request.expected_tip
            )
        if remote_tip is not None:
            self.git.delete_remote_ref(
                request.repository,
                request.remote,
                request.remote_ref,
                request.expected_tip,
            )
