from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


class GitInspectionError(RuntimeError):
    """Git repository facts could not be read reliably."""


class UnsafeWorktreePath(RuntimeError):
    """A delivery worktree would escape the runner-owned directory."""


class WorktreeCollision(RuntimeError):
    """A deterministic delivery path is not owned by this runner."""


class RemoteDivergence(RuntimeError):
    """A remote branch no longer has the expected compare-and-swap value."""


class PublicationState(Enum):
    PUBLISHED = "published"
    ALREADY_PUBLISHED = "already_published"


def _run(repository_path: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repository_path), *args),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitInspectionError(f"could not run git: {error}") from error
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise GitInspectionError(detail or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def _succeeds(repository_path: Path, *args: str) -> bool:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repository_path), *args),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitInspectionError(f"could not run git: {error}") from error
    return completed.returncode == 0


def _repository_from_url(url: str) -> str:
    value = url.strip().removesuffix(".git").rstrip("/")
    match = re.search(r"(?:github\.com[/:])([^/:\s]+/[^/\s]+)$", value, re.IGNORECASE)
    if match is None:
        raise GitInspectionError(f"remote URL is not a recognizable GitHub repository: {url}")
    return match.group(1)


class GitInspector:
    def inspect(
        self, repository_path: Path, remote: str, branches: tuple[str, ...]
    ) -> str:
        if not repository_path.is_dir():
            raise GitInspectionError(f"repository path is not a directory: {repository_path}")
        if _run(repository_path, "rev-parse", "--is-inside-work-tree") != "true":
            raise GitInspectionError(f"path is not a Git worktree: {repository_path}")
        identity = _repository_from_url(_run(repository_path, "remote", "get-url", remote))
        for branch in dict.fromkeys(branches):
            remote_ref = f"refs/remotes/{remote}/{branch}"
            local_ref = f"refs/heads/{branch}"
            if subprocess.run(
                ("git", "-C", str(repository_path), "show-ref", "--verify", "--quiet", remote_ref),
                check=False,
                timeout=30,
            ).returncode != 0 and subprocess.run(
                ("git", "-C", str(repository_path), "show-ref", "--verify", "--quiet", local_ref),
                check=False,
                timeout=30,
            ).returncode != 0:
                raise GitInspectionError(f"configured branch is not readable: {branch}")
        return identity


class DeliveryGit(Protocol):
    def fetch(self, repository: Path, remote: str) -> None: ...

    def resolve_remote_branch(
        self, repository: Path, remote: str, branch: str
    ) -> str: ...

    def create_worktree(
        self, repository: Path, worktree: Path, branch: str, start_sha: str
    ) -> None: ...


class GitOperations:
    def fetch(self, repository: Path, remote: str) -> None:
        _run(repository, "fetch", "--prune", remote)

    def resolve_remote_branch(
        self, repository: Path, remote: str, branch: str
    ) -> str:
        return _run(repository, "rev-parse", "--verify", f"{remote}/{branch}^{{commit}}")

    def create_worktree(
        self, repository: Path, worktree: Path, branch: str, start_sha: str
    ) -> None:
        _run(repository, "worktree", "add", "-b", branch, str(worktree), start_sha)

    def verify_worktree(self, worktree: Path, branch: str) -> None:
        if _run(worktree, "rev-parse", "--is-inside-work-tree") != "true":
            raise WorktreeCollision(f"path is not a Git worktree: {worktree}")
        current = _run(worktree, "symbolic-ref", "--quiet", "--short", "HEAD")
        if current != branch:
            raise WorktreeCollision(
                f"worktree uses branch {current!r}, expected owned branch {branch!r}"
            )

    def tracked_changes(self, worktree: Path) -> tuple[str, ...]:
        output = _run(
            worktree,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        return tuple(line for line in output.splitlines() if line)

    def head_commit(self, worktree: Path) -> str:
        return _run(worktree, "rev-parse", "--verify", "HEAD^{commit}")

    def resolve_local_branch(self, repository: Path, branch: str) -> str:
        return _run(
            repository,
            "rev-parse",
            "--verify",
            f"refs/heads/{branch}^{{commit}}",
        )

    def is_ancestor(self, repository: Path, ancestor: str, descendant: str) -> bool:
        return _succeeds(
            repository,
            "merge-base",
            "--is-ancestor",
            f"{ancestor}^{{commit}}",
            f"{descendant}^{{commit}}",
        )

    def commit_count(self, repository: Path, base: str, head: str) -> int:
        output = _run(
            repository,
            "rev-list",
            "--count",
            f"{base}^{{commit}}..{head}^{{commit}}",
        )
        try:
            return int(output)
        except ValueError as error:
            raise GitInspectionError(f"invalid commit count from git: {output!r}") from error

    def require_single_direct_child(
        self, worktree: Path, base_head: str, result_commit: str
    ) -> str:
        dirty = self.tracked_changes(worktree)
        if dirty:
            raise WorktreeCollision(
                "owned feature worktree has dirty tracked state: "
                + ", ".join(dirty)
            )
        head = self.head_commit(worktree)
        if head != result_commit:
            raise WorktreeCollision(
                f"result commit {result_commit} does not match worktree HEAD {head}"
            )
        if (
            self.commit_count(worktree, base_head, result_commit) != 1
            or not self.is_ancestor(worktree, base_head, result_commit)
        ):
            raise WorktreeCollision(
                f"result must contain exactly one commit after base {base_head}"
            )
        parents = _run(
            worktree,
            "rev-list",
            "--parents",
            "-n",
            "1",
            f"{result_commit}^{{commit}}",
        ).split()
        if len(parents) != 2 or parents[1] != base_head:
            raise WorktreeCollision(
                f"result commit {result_commit} must be a direct child of base {base_head}"
            )
        return result_commit

    def remote_branch_head(
        self, repository: str | Path, remote: str, branch: str
    ) -> str | None:
        output = _run(Path(repository), "ls-remote", "--heads", remote, f"refs/heads/{branch}")
        if not output:
            return None
        fields = output.splitlines()
        if len(fields) != 1 or len(fields[0].split()) != 2:
            raise GitInspectionError(f"remote branch result is ambiguous: {branch}")
        return fields[0].split()[0]

    def push_branch(
        self, repository: str | Path, remote: str, branch: str, expected_head: str
    ) -> None:
        path = Path(repository)
        local_head = _run(path, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}")
        if local_head != expected_head:
            raise GitInspectionError(
                f"local branch head {local_head} does not match expected {expected_head}"
            )
        _run(path, "push", remote, f"refs/heads/{branch}:refs/heads/{branch}")

    def publish_branch(
        self,
        repository: str | Path,
        remote: str,
        branch: str,
        *,
        expected_remote_head: str,
        new_head: str,
    ) -> PublicationState:
        path = Path(repository)
        self.verify_worktree(path, branch)
        if self.tracked_changes(path):
            raise WorktreeCollision(
                "owned feature worktree has dirty tracked state and cannot be published"
            )
        local_head = self.head_commit(path)
        if local_head != new_head:
            raise WorktreeCollision(
                f"publication commit {new_head} does not match worktree HEAD {local_head}"
            )
        remote_head = self.remote_branch_head(path, remote, branch)
        if remote_head == new_head:
            return PublicationState.ALREADY_PUBLISHED
        if remote_head != expected_remote_head:
            raise RemoteDivergence(
                f"remote branch {branch} is at {remote_head}, "
                f"expected {expected_remote_head}"
            )
        if not self.is_ancestor(path, expected_remote_head, new_head):
            raise WorktreeCollision(
                f"publication commit {new_head} does not descend from "
                f"expected remote head {expected_remote_head}"
            )
        try:
            _run(
                path,
                "push",
                f"--force-with-lease=refs/heads/{branch}:{expected_remote_head}",
                remote,
                f"{new_head}:refs/heads/{branch}",
            )
        except GitInspectionError as error:
            observed = self.remote_branch_head(path, remote, branch)
            if observed == new_head:
                return PublicationState.ALREADY_PUBLISHED
            raise RemoteDivergence(
                f"remote branch {branch} changed during publication to {observed}; "
                f"expected {expected_remote_head}"
            ) from error
        observed = self.remote_branch_head(path, remote, branch)
        if observed != new_head:
            raise RemoteDivergence(
                f"remote branch {branch} is at {observed} after publication, "
                f"expected {new_head}"
            )
        return PublicationState.PUBLISHED

    def prepare_integration_worktree(
        self,
        repository: Path,
        worktree: Path,
        feature_branch: str,
        expected_head: str,
    ) -> None:
        ownership_file = (
            worktree.parent.parent / "ownership" / f"{worktree.name}.json"
        )
        expected_ownership = {
            "branch": feature_branch,
            "worktree": str(worktree),
        }
        if worktree.exists():
            try:
                ownership = json.loads(ownership_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise WorktreeCollision(
                    f"existing integration worktree is not runner-owned: {worktree}"
                ) from error
            if ownership != expected_ownership:
                raise WorktreeCollision(
                    f"integration worktree ownership does not match: {worktree}"
                )
            self.verify_worktree(worktree, feature_branch)
            head = _run(worktree, "rev-parse", "--verify", "HEAD^{commit}")
            if head != expected_head:
                raise WorktreeCollision(
                    f"integration worktree head {head} does not match expected {expected_head}"
                )
            return
        worktree.parent.mkdir(parents=True, exist_ok=True)
        local_head = _run(
            repository,
            "rev-parse",
            "--verify",
            f"refs/heads/{feature_branch}^{{commit}}",
        )
        if local_head != expected_head:
            raise GitInspectionError(
                f"feature branch head {local_head} does not match expected {expected_head}"
            )
        ownership_file.parent.mkdir(parents=True, exist_ok=True)
        ownership_file.write_text(
            json.dumps(
                expected_ownership,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        _run(repository, "worktree", "add", str(worktree), feature_branch)

    def merge_base_into_feature(
        self, worktree: Path, base_ref: str, feature_branch: str
    ) -> str:
        self.verify_worktree(worktree, feature_branch)
        if _run(worktree, "status", "--porcelain=v1", "--untracked-files=no"):
            raise GitInspectionError("integration worktree has tracked changes")
        _run(worktree, "merge", "--no-edit", "--no-ff", base_ref)
        return _run(worktree, "rev-parse", "--verify", "HEAD^{commit}")


@dataclass(frozen=True, slots=True)
class DeliveryIdentity:
    parent_issue: int
    issue_number: int
    delivery: int

    def __post_init__(self) -> None:
        if min(self.parent_issue, self.issue_number, self.delivery) <= 0:
            raise ValueError("delivery identity values must be positive")

    @property
    def slug(self) -> str:
        return f"p-{self.parent_issue}-i-{self.issue_number}-d-{self.delivery}"


@dataclass(frozen=True, slots=True)
class DeliveryLayout:
    worktree: Path
    branch: str


@dataclass(frozen=True, slots=True)
class PreparedDelivery:
    worktree: Path
    branch: str
    base_sha: str


@dataclass(frozen=True, slots=True)
class PreparedFeatureBranch:
    worktree: Path
    branch: str
    base_head: str
    head: str


class FeatureBranchWorkspace:
    def __init__(
        self, runtime_root: Path, *, git: GitOperations | None = None
    ) -> None:
        self.runtime_root = runtime_root.resolve()
        self.worktree_root = self.runtime_root / "feature-worktrees"
        self._ownership_root = self.runtime_root / "ownership"
        self.git = git or GitOperations()

    def _worktree(self, parent_issue: int) -> Path:
        if parent_issue <= 0:
            raise ValueError("parent issue must be positive")
        return self.worktree_root / f"p-{parent_issue}"

    def _ownership_file(self, parent_issue: int) -> Path:
        return self._ownership_root / f"feature-p-{parent_issue}.json"

    def _expected_ownership(
        self,
        *,
        repository: Path,
        worktree: Path,
        feature_branch: str,
        parent_issue: int,
        base_head: str,
    ) -> dict[str, object]:
        common_dir = _run(repository, "rev-parse", "--path-format=absolute", "--git-common-dir")
        return {
            "base_head": base_head,
            "branch": feature_branch,
            "git_common_dir": str(Path(common_dir).resolve()),
            "parent_issue": parent_issue,
            "worktree": str(worktree),
        }

    def _read_owned(
        self,
        *,
        repository: Path,
        parent_issue: int,
        feature_branch: str,
        expected_base: str | None = None,
    ) -> tuple[Path, dict[str, object]]:
        worktree = self._worktree(parent_issue)
        ownership_file = self._ownership_file(parent_issue)
        try:
            ownership = json.loads(ownership_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise WorktreeCollision(
                f"feature worktree ownership is missing or invalid: {worktree}"
            ) from error
        base_head = ownership.get("base_head")
        if not isinstance(base_head, str) or not base_head:
            raise WorktreeCollision("feature worktree ownership has no base commit")
        expected = self._expected_ownership(
            repository=repository,
            worktree=worktree,
            feature_branch=feature_branch,
            parent_issue=parent_issue,
            base_head=base_head,
        )
        if ownership != expected:
            raise WorktreeCollision(
                f"feature worktree ownership does not match: {worktree}"
            )
        if expected_base is not None and base_head != expected_base:
            raise WorktreeCollision(
                f"owned feature worktree base {base_head} does not match "
                f"expected remote head {expected_base}"
            )
        if not worktree.is_dir():
            raise WorktreeCollision(
                f"owned feature worktree is missing: {worktree}"
            )
        self.git.verify_worktree(worktree, feature_branch)
        return worktree, ownership

    def _branch_checkout(self, repository: Path, branch: str) -> Path | None:
        output = _run(repository, "worktree", "list", "--porcelain")
        current_path: Path | None = None
        expected_ref = f"refs/heads/{branch}"
        for line in (*output.splitlines(), ""):
            if line.startswith("worktree "):
                current_path = Path(line.removeprefix("worktree ")).resolve()
            elif line == f"branch {expected_ref}":
                return current_path
            elif not line:
                current_path = None
        return None

    def prepare(
        self,
        *,
        repository: Path,
        remote: str,
        feature_branch: str,
        parent_issue: int,
        expected_remote_head: str,
    ) -> PreparedFeatureBranch:
        repository = repository.resolve()
        worktree = self._worktree(parent_issue)
        ownership_file = self._ownership_file(parent_issue)
        if worktree.exists() or ownership_file.exists():
            owned, ownership = self._read_owned(
                repository=repository,
                parent_issue=parent_issue,
                feature_branch=feature_branch,
            )
            dirty = self.git.tracked_changes(owned)
            if dirty:
                raise WorktreeCollision(
                    "owned feature worktree is dirty: " + ", ".join(dirty)
                )
            head = self.git.head_commit(owned)
            owned_base = ownership["base_head"]
            if not isinstance(owned_base, str):
                raise WorktreeCollision("feature worktree ownership has no base commit")
            remote_head = self.git.remote_branch_head(
                repository, remote, feature_branch
            )
            if owned_base != expected_remote_head:
                if head != expected_remote_head or remote_head != expected_remote_head:
                    raise WorktreeCollision(
                        f"owned feature worktree base {owned_base} does not match "
                        f"expected batch base {expected_remote_head}"
                    )
                ownership = dict(ownership)
                ownership["base_head"] = expected_remote_head
                ownership_file.write_text(
                    json.dumps(ownership, sort_keys=True, separators=(",", ":")),
                    encoding="utf-8",
                )
                owned_base = expected_remote_head
            if not self.git.is_ancestor(owned, owned_base, head):
                raise WorktreeCollision(
                    f"owned feature worktree HEAD {head} has diverged from "
                    f"base {owned_base}"
                )
            if remote_head not in (owned_base, head):
                raise RemoteDivergence(
                    f"remote branch {feature_branch} is at {remote_head}, "
                    f"expected unpublished base {owned_base} "
                    f"or owned HEAD {head}"
                )
            return PreparedFeatureBranch(
                owned, feature_branch, owned_base, head
            )

        remote_head = self.git.remote_branch_head(repository, remote, feature_branch)
        if remote_head != expected_remote_head:
            raise RemoteDivergence(
                f"remote branch {feature_branch} is at {remote_head}, "
                f"expected {expected_remote_head}"
            )
        checked_out = self._branch_checkout(repository, feature_branch)
        if checked_out is not None:
            raise WorktreeCollision(
                f"feature branch {feature_branch!r} is already checked out "
                f"in unowned worktree {checked_out}"
            )
        local_ref = f"refs/heads/{feature_branch}"
        if _succeeds(repository, "show-ref", "--verify", "--quiet", local_ref):
            local_head = _run(
                repository, "rev-parse", "--verify", f"{local_ref}^{{commit}}"
            )
            if local_head != expected_remote_head:
                raise WorktreeCollision(
                    f"local feature branch {feature_branch!r} is at {local_head}, "
                    f"expected {expected_remote_head}; refusing to discard commits"
                )

        self.worktree_root.mkdir(parents=True, exist_ok=True)
        self._ownership_root.mkdir(parents=True, exist_ok=True)
        if _succeeds(repository, "show-ref", "--verify", "--quiet", local_ref):
            _run(repository, "worktree", "add", str(worktree), feature_branch)
        else:
            _run(
                repository,
                "worktree",
                "add",
                "-b",
                feature_branch,
                str(worktree),
                expected_remote_head,
            )
        ownership = self._expected_ownership(
            repository=repository,
            worktree=worktree,
            feature_branch=feature_branch,
            parent_issue=parent_issue,
            base_head=expected_remote_head,
        )
        try:
            ownership_file.write_text(
                json.dumps(ownership, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
        except OSError:
            _run(repository, "worktree", "remove", str(worktree))
            raise
        return PreparedFeatureBranch(
            worktree, feature_branch, expected_remote_head, expected_remote_head
        )

    def reset(
        self,
        *,
        parent_issue: int,
        feature_branch: str,
        target_head: str,
    ) -> None:
        worktree = self._worktree(parent_issue)
        ownership_file = self._ownership_file(parent_issue)
        try:
            ownership = json.loads(ownership_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise WorktreeCollision(
                f"feature worktree ownership is missing or invalid: {worktree}"
            ) from error
        if (
            ownership.get("parent_issue") != parent_issue
            or ownership.get("branch") != feature_branch
            or ownership.get("worktree") != str(worktree)
        ):
            raise WorktreeCollision(
                f"feature worktree ownership does not match: {worktree}"
            )
        base_head = ownership.get("base_head")
        if not isinstance(base_head, str):
            raise WorktreeCollision("feature worktree ownership has no base commit")
        self.git.verify_worktree(worktree, feature_branch)
        current_head = self.git.head_commit(worktree)
        if (
            not self.git.is_ancestor(worktree, base_head, target_head)
            or not self.git.is_ancestor(worktree, target_head, current_head)
        ):
            raise WorktreeCollision(
                f"rollback target {target_head} is outside owned commit range "
                f"{base_head}..{current_head}"
            )
        common_dir = _run(
            worktree,
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        )
        if ownership.get("git_common_dir") != str(Path(common_dir).resolve()):
            raise WorktreeCollision(
                f"feature worktree repository ownership does not match: {worktree}"
            )
        _run(worktree, "reset", "--hard", target_head)
        _run(worktree, "clean", "-fd")


class DeliveryWorkspace:
    def __init__(self, runtime_root: Path, *, git: DeliveryGit | None = None) -> None:
        self.runtime_root = runtime_root.resolve()
        self.worktree_root = self.runtime_root / "worktrees"
        self._ownership_root = self.runtime_root / "ownership"
        self.git = git or GitOperations()

    def layout(self, identity: DeliveryIdentity) -> DeliveryLayout:
        return DeliveryLayout(
            self.worktree_root / identity.slug,
            f"runner/p-{identity.parent_issue}/i-{identity.issue_number}/d-{identity.delivery}",
        )

    def assert_safe_path(self, path: Path) -> None:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.worktree_root):
            raise UnsafeWorktreePath(
                f"delivery worktree must remain beneath the runtime root: {resolved}"
            )

    def _ownership_file(self, identity: DeliveryIdentity) -> Path:
        return self._ownership_root / f"{identity.slug}.json"

    def prepare(
        self,
        *,
        repository: Path,
        remote: str,
        feature_branch: str,
        identity: DeliveryIdentity,
    ) -> PreparedDelivery:
        layout = self.layout(identity)
        self.assert_safe_path(layout.worktree)
        self.git.fetch(repository, remote)
        remote_sha = self.git.resolve_remote_branch(repository, remote, feature_branch)
        ownership_file = self._ownership_file(identity)
        if layout.worktree.exists():
            try:
                ownership = json.loads(ownership_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise WorktreeCollision(
                    f"existing worktree is not recognized as runner-owned: {layout.worktree}"
                ) from error
            expected = {
                "branch": layout.branch,
                "worktree": str(layout.worktree),
            }
            if any(ownership.get(key) != value for key, value in expected.items()):
                raise WorktreeCollision(
                    f"existing worktree ownership does not match delivery: {layout.worktree}"
                )
            base_sha = ownership.get("base_sha")
            if not isinstance(base_sha, str) or not base_sha:
                raise WorktreeCollision("delivery ownership record has no base commit")
            verifier = getattr(self.git, "verify_worktree", None)
            if verifier is None:
                raise WorktreeCollision(
                    "Git backend cannot verify an existing delivery worktree"
                )
            verifier(layout.worktree, layout.branch)
            return PreparedDelivery(layout.worktree, layout.branch, base_sha)

        self.worktree_root.mkdir(parents=True, exist_ok=True)
        self._ownership_root.mkdir(parents=True, exist_ok=True)
        self.git.create_worktree(repository, layout.worktree, layout.branch, remote_sha)
        ownership_file.write_text(
            json.dumps(
                {
                    "base_sha": remote_sha,
                    "branch": layout.branch,
                    "worktree": str(layout.worktree),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        return PreparedDelivery(layout.worktree, layout.branch, remote_sha)
