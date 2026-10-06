from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from local_issue_runner.agent_backend import (
    CopilotResult,
    CopilotResultError,
    DeliveryAgent,
    parse_copilot_result,
    require_single_commit,
)
from local_issue_runner.git_ops import (
    DeliveryIdentity,
    DeliveryWorkspace,
    UnsafeWorktreePath,
)
from local_issue_runner.scope import ChildScopeInput, build_child_scope_manifest
from local_issue_runner.validation import (
    ExactTreeValidator,
    TrackedTreeChanged,
    ValidationCommand,
)

REMOTE_FEATURE_SHA = "a" * 40
COMMITTED_HEAD = "b" * 40
COMMITTED_TREE = "c" * 40


@dataclass
class RecordingGit:
    remote_sha: str = REMOTE_FEATURE_SHA
    head: str = COMMITTED_HEAD
    tree: str = COMMITTED_TREE
    dirty_paths: tuple[str, ...] = ()
    calls: list[tuple[object, ...]] = field(default_factory=list)

    def fetch(self, repository: Path, remote: str) -> None:
        self.calls.append(("fetch", repository, remote))

    def resolve_remote_branch(
        self, repository: Path, remote: str, branch: str
    ) -> str:
        self.calls.append(("resolve_remote_branch", repository, remote, branch))
        return self.remote_sha

    def create_worktree(
        self, repository: Path, worktree: Path, branch: str, start_sha: str
    ) -> None:
        self.calls.append(
            ("create_worktree", repository, worktree, branch, start_sha)
        )
        worktree.mkdir(parents=True)

    def tracked_changes(self, worktree: Path) -> tuple[str, ...]:
        self.calls.append(("tracked_changes", worktree))
        return self.dirty_paths

    def head_commit(self, worktree: Path) -> str:
        self.calls.append(("head_commit", worktree))
        return self.head

    def tree_id(self, worktree: Path, revision: str) -> str:
        self.calls.append(("tree_id", worktree, revision))
        return self.tree


def identity() -> DeliveryIdentity:
    return DeliveryIdentity(parent_issue=100, issue_number=101, delivery=2)


def test_workspace_fetches_and_uses_latest_remote_feature_sha(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "developer-checkout"
    repository.mkdir()
    git = RecordingGit()
    workspace = DeliveryWorkspace(tmp_path / "runtime", git=git)

    prepared = workspace.prepare(
        repository=repository,
        remote="upstream",
        feature_branch="feature/100",
        identity=identity(),
    )

    assert prepared.base_sha == REMOTE_FEATURE_SHA
    assert git.calls[:3] == [
        ("fetch", repository, "upstream"),
        (
            "resolve_remote_branch",
            repository,
            "upstream",
            "feature/100",
        ),
        (
            "create_worktree",
            repository,
            tmp_path / "runtime" / "worktrees" / "p-100-i-101-d-2",
            "runner/p-100/i-101/d-2",
            REMOTE_FEATURE_SHA,
        ),
    ]


def test_delivery_branch_and_worktree_names_are_deterministic(
    tmp_path: Path,
) -> None:
    first = DeliveryWorkspace(tmp_path / "runtime", git=RecordingGit()).layout(
        identity()
    )
    second = DeliveryWorkspace(tmp_path / "runtime", git=RecordingGit()).layout(
        identity()
    )

    assert first == second
    assert first.branch == "runner/p-100/i-101/d-2"
    assert first.worktree == (
        tmp_path / "runtime" / "worktrees" / "p-100-i-101-d-2"
    )


@pytest.mark.parametrize(
    "unsafe_path",
    [
        Path("..") / "outside",
        Path("worktrees") / ".." / ".." / "outside",
    ],
)
def test_delivery_worktree_must_remain_under_runtime_root(
    tmp_path: Path, unsafe_path: Path
) -> None:
    workspace = DeliveryWorkspace(tmp_path / "runtime", git=RecordingGit())

    with pytest.raises(UnsafeWorktreePath, match="runtime root"):
        workspace.assert_safe_path(tmp_path / "runtime" / unsafe_path)


def test_scope_manifest_is_canonical_child_specific_and_sha256_hashed() -> None:
    source = ChildScopeInput(
        repository="owner/project",
        parent_issue=100,
        issue_number=101,
        delivery=2,
        title="Implement child",
        body="The exact child requirements.",
        feature_branch="feature/100",
        prerequisites=(
            {"repository": "owner/project", "issue": 99, "delivery": 3},
        ),
        acceptance_criteria=("returns the expected value", "tests pass"),
        linked_specs=(
            {"path": "docs/spec.md", "git_blob": "blob-123"},
        ),
        decisions=(
            {"url": "https://github.com/owner/project/issues/101#issuecomment-7",
             "content_hash": "decision-456"},
        ),
    )

    manifest = build_child_scope_manifest(source)
    encoded = manifest.canonical_json

    assert encoded == json.dumps(
        manifest.content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert manifest.fingerprint == hashlib.sha256(encoded).hexdigest()
    assert manifest.content["issue"]["number"] == 101
    assert manifest.content["feature"]["parent_issue"] == 100
    assert "selected_children" not in manifest.content


@dataclass
class RecordingLauncher:
    result: str
    calls: list[tuple[Path, bytes]] = field(default_factory=list)

    def __call__(self, worktree: Path, scope_json: bytes) -> str:
        self.calls.append((worktree, scope_json))
        return self.result


def successful_result() -> str:
    return json.dumps(
        {
            "outcome": "success",
            "summary": "Implemented and committed the child issue.",
            "commit_ids": [COMMITTED_HEAD],
            "validation_summary": "Targeted tests passed.",
            "feedback_dispositions": [],
            "blockers": [],
        }
    )


def test_delivery_starts_at_most_one_coding_job() -> None:
    launcher = RecordingLauncher(successful_result())
    agent = DeliveryAgent(launcher=launcher)
    worktree = Path("runtime/worktrees/p-100-i-101-d-2")
    scope_json = b'{"delivery":2}'

    first = agent.run_once("owner/project:100:101:2", worktree, scope_json)
    second = agent.run_once("owner/project:100:101:2", worktree, scope_json)

    assert first == second
    assert launcher.calls == [(worktree, scope_json)]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"outcome": "unknown"},
        {
            "outcome": "success",
            "summary": "claims success without a deliverable",
            "commit_ids": [],
            "validation_summary": "passed",
            "feedback_dispositions": [],
            "blockers": [],
        },
        {
            "outcome": "success",
            "summary": "ok",
            "commit_ids": [COMMITTED_HEAD],
            "validation_summary": "passed",
            "feedback_dispositions": [],
            "blockers": [],
            "unsupported": True,
        },
    ],
)
def test_copilot_result_rejects_missing_malformed_or_unjustified_success(
    payload: object,
) -> None:
    raw = "" if payload is None else json.dumps(payload)

    with pytest.raises(CopilotResultError):
        parse_copilot_result(raw)


def test_copilot_result_accepts_the_exact_structured_schema() -> None:
    result = parse_copilot_result(successful_result())

    assert result.outcome == "success"
    assert result.commit_ids == (COMMITTED_HEAD,)
    assert result.blockers == ()


def test_local_delivery_requires_exactly_one_successful_commit() -> None:
    result = parse_copilot_result(successful_result())

    assert require_single_commit(result) == COMMITTED_HEAD

    with pytest.raises(CopilotResultError, match="exactly one"):
        require_single_commit(
            CopilotResult(
                outcome="success",
                summary="Created two commits.",
                commit_ids=(REMOTE_FEATURE_SHA, COMMITTED_HEAD),
                validation_summary="passed",
                feedback_dispositions=(),
                blockers=(),
            )
        )

    with pytest.raises(CopilotResultError, match="successful"):
        require_single_commit(
            CopilotResult(
                outcome="blocked",
                summary="Blocked.",
                commit_ids=(),
                validation_summary="not run",
                feedback_dispositions=(),
                blockers=("missing dependency",),
            )
        )


def test_validation_record_is_bound_to_exact_clean_committed_tree(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    git = RecordingGit()
    commands: list[tuple[Path, tuple[str, ...]]] = []

    def run_command(path: Path, argv: tuple[str, ...]) -> tuple[int, str, str]:
        commands.append((path, argv))
        return 0, "12 passed", ""

    validator = ExactTreeValidator(git=git, run_command=run_command)
    record = validator.validate(
        worktree=worktree,
        base_commit=REMOTE_FEATURE_SHA,
        scope_fingerprint="scope-fingerprint",
        commands=(ValidationCommand(("uv", "run", "pytest", "tests/unit")),),
    )

    assert commands == [(worktree, ("uv", "run", "pytest", "tests/unit"))]
    assert record.head_commit == COMMITTED_HEAD
    assert record.tree_id == COMMITTED_TREE
    assert record.base_commit == REMOTE_FEATURE_SHA
    assert record.scope_fingerprint == "scope-fingerprint"
    assert record.commands[0].exit_code == 0
    assert record.commands[0].stdout == "12 passed"
    assert len(record.validation_config_fingerprint) == 64


def test_validation_rejects_a_command_that_modifies_tracked_files(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    git = RecordingGit()

    def dirtying_command(
        _path: Path, _argv: tuple[str, ...]
    ) -> tuple[int, str, str]:
        git.dirty_paths = ("src/generated.py",)
        return 0, "passed", ""

    validator = ExactTreeValidator(git=git, run_command=dirtying_command)

    with pytest.raises(TrackedTreeChanged, match="src/generated.py"):
        validator.validate(
            worktree=worktree,
            base_commit=REMOTE_FEATURE_SHA,
            scope_fingerprint="scope-fingerprint",
            commands=(ValidationCommand(("generate-and-test",)),),
        )


def test_dirty_developer_checkout_is_never_mutated_or_used_as_worktree(
    tmp_path: Path,
) -> None:
    checkout = tmp_path / "developer-checkout"
    checkout.mkdir()
    dirty_file = checkout / "unfinished.txt"
    dirty_file.write_text("developer work\n", encoding="utf-8")
    git = RecordingGit()
    workspace = DeliveryWorkspace(tmp_path / "runtime", git=git)

    prepared = workspace.prepare(
        repository=checkout,
        remote="origin",
        feature_branch="feature/100",
        identity=identity(),
    )

    assert dirty_file.read_text(encoding="utf-8") == "developer work\n"
    assert prepared.worktree != checkout
    mutating_calls = [
        call
        for call in git.calls
        if call[0] in {"create_worktree", "checkout", "reset", "clean", "stash"}
    ]
    assert mutating_calls == [
        (
            "create_worktree",
            checkout,
            prepared.worktree,
            prepared.branch,
            REMOTE_FEATURE_SHA,
        )
    ]
