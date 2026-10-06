from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from local_issue_runner.db import CoordinatorStore
from local_issue_runner.integration import (
    ChildIntegrationFact,
    IntegrationBlocked,
    IntegrationCoordinator,
    IntegrationDefect,
    IntegrationIdentity,
    IntegrationPullRequest,
    IntegrationRequest,
    ParentReadiness,
    evaluate_parent_readiness,
    integration_pr_ownership_marker,
    integration_pr_owned_by_parent,
    removed_scope_code_blocker,
)
from local_issue_runner.models import (
    IntegrationRepairChildRecord,
    ParentDeliveryRecord,
)
from local_issue_runner.validation import ValidationCommand, ValidationRecord

FEATURE_HEAD = "a" * 40
MAIN_HEAD = "b" * 40


def identity() -> IntegrationIdentity:
    return IntegrationIdentity(
        repository="owner/project",
        parent_issue=100,
        delivery=1,
    )


def request() -> IntegrationRequest:
    return IntegrationRequest(
        identity=identity(),
        feature_branch="feature/customer-export",
        feature_head=FEATURE_HEAD,
        main_branch="main",
        title="Integrate customer export",
        body="Completes parent #100.",
    )


def integration_pr(
    *,
    number: int = 41,
    body: str | None = None,
    head_ref: str = "feature/customer-export",
    head_sha: str = FEATURE_HEAD,
    base_ref: str = "main",
) -> IntegrationPullRequest:
    return IntegrationPullRequest(
        number=number,
        state="OPEN",
        head_ref=head_ref,
        head_sha=head_sha,
        base_ref=base_ref,
        body=body if body is not None else integration_pr_ownership_marker(identity()),
        merged=False,
    )


def child(
    issue: int,
    *,
    complete: bool = False,
    excluded: bool = False,
    outstanding_work: bool = False,
    blocking_relationship: bool = False,
) -> ChildIntegrationFact:
    return ChildIntegrationFact(
        issue_number=issue,
        verifiably_complete=complete,
        explicitly_excluded=excluded,
        outstanding_work=outstanding_work,
        unresolved_blocking_relationship=blocking_relationship,
    )


def test_parent_is_ready_only_when_the_full_refreshed_child_set_is_resolved() -> None:
    ready = evaluate_parent_readiness(
        selected_issue_numbers=(101, 102, 103),
        children=(
            child(101, complete=True),
            child(102, excluded=True),
            child(103, complete=True),
        ),
    )

    assert ready is ParentReadiness.READY

    for unresolved in (
        (child(101, complete=True), child(102, excluded=True)),
        (
            child(101, complete=True),
            child(102, excluded=True),
            child(103, outstanding_work=True),
        ),
        (
            child(101, complete=True),
            child(102, excluded=True),
            child(103, complete=True, blocking_relationship=True),
        ),
    ):
        assert (
            evaluate_parent_readiness(
                selected_issue_numbers=(101, 102, 103),
                children=unresolved,
            )
            is ParentReadiness.CHILD_WORK
        )


def test_parent_readiness_rejects_invalid_selected_child_identity() -> None:
    with pytest.raises(ValueError, match="positive"):
        evaluate_parent_readiness(
            selected_issue_numbers=(0,),
            children=(),
        )
    with pytest.raises(ValueError, match="unique"):
        evaluate_parent_readiness(
            selected_issue_numbers=(101, 101),
            children=(child(101, complete=True),),
        )


@dataclass
class FakeGit:
    calls: list[tuple[object, ...]] = field(default_factory=list)

    def prepare_integration_worktree(
        self,
        repository: Path,
        worktree: Path,
        feature_branch: str,
        expected_head: str,
    ) -> None:
        self.calls.append(
            ("prepare", repository, worktree, feature_branch, expected_head)
        )
        worktree.mkdir(parents=True)

    def merge_base_into_feature(
        self, worktree: Path, base_ref: str, feature_branch: str
    ) -> str:
        self.calls.append(("merge", worktree, base_ref, feature_branch))
        return "c" * 40

    def push_branch(
        self, repository: Path, remote: str, branch: str, expected_head: str
    ) -> None:
        self.calls.append(("push", repository, remote, branch, expected_head))


@dataclass
class FakeGitHub:
    pull_requests: list[IntegrationPullRequest] = field(default_factory=list)
    creates: list[IntegrationRequest] = field(default_factory=list)
    repair_creates: list[tuple[IntegrationIdentity, IntegrationDefect]] = field(
        default_factory=list
    )
    repair_attachments: list[bool] = field(default_factory=list)

    def list_integration_pull_requests(
        self, repository: str, feature_branch: str, main_branch: str
    ) -> tuple[IntegrationPullRequest, ...]:
        return tuple(self.pull_requests)

    def create_integration_pull_request(
        self, candidate: IntegrationRequest
    ) -> IntegrationPullRequest:
        self.creates.append(candidate)
        created = integration_pr(number=50, body=candidate.body)
        self.pull_requests.append(created)
        return created

    def create_integration_repair_child(
        self,
        owner: IntegrationIdentity,
        defect: IntegrationDefect,
        *,
        attach_to_parent: bool,
    ) -> int:
        self.repair_creates.append((owner, defect))
        self.repair_attachments.append(attach_to_parent)
        return 901


@dataclass
class FakeStore:
    created_prs: set[int] = field(default_factory=set)
    quiet_restarts: list[tuple[int, str]] = field(default_factory=list)
    repair_children: dict[str, int] = field(default_factory=dict)
    repair_origin: IntegrationRepairChildRecord | None = None
    parent_deliveries: list[ParentDeliveryRecord] = field(default_factory=list)

    def record_parent_delivery(self, record: ParentDeliveryRecord) -> None:
        self.parent_deliveries.append(record)

    def record_integration_pr_creation(
        self, owner: IntegrationIdentity, pull_request: IntegrationPullRequest
    ) -> None:
        self.created_prs.add(pull_request.number)

    def is_runner_created_integration_pr(
        self, owner: IntegrationIdentity, pull_request: IntegrationPullRequest
    ) -> bool:
        return pull_request.number in self.created_prs

    def restart_quiet_period(self, pr_number: int, revision: str) -> None:
        self.quiet_restarts.append((pr_number, revision))

    def repair_child_for_finding(
        self, repository: str, parent_issue: int, finding_identity: str
    ) -> IntegrationRepairChildRecord | None:
        if self.repair_origin is not None:
            return self.repair_origin
        issue_number = self.repair_children.get(finding_identity)
        if issue_number is None:
            return None
        return IntegrationRepairChildRecord(
            repository, parent_issue, finding_identity, issue_number, "aggregate validation"
        )

    def record_repair_child(self, record: IntegrationRepairChildRecord) -> None:
        self.repair_children[record.finding_identity] = record.issue_number


@dataclass
class FakeValidator:
    calls: list[tuple[Path, str, str, tuple[ValidationCommand, ...]]] = field(
        default_factory=list
    )

    def validate(
        self,
        *,
        worktree: Path,
        base_commit: str,
        scope_fingerprint: str,
        commands: tuple[ValidationCommand, ...],
    ) -> ValidationRecord:
        self.calls.append((worktree, base_commit, scope_fingerprint, commands))
        return ValidationRecord(
            head_commit=FEATURE_HEAD,
            tree_id="d" * 40,
            base_commit=base_commit,
            scope_fingerprint=scope_fingerprint,
            validation_config_fingerprint="e" * 64,
            commands=(),
        )


def coordinator(
    tmp_path: Path,
    *,
    github: FakeGitHub | None = None,
) -> tuple[IntegrationCoordinator, FakeGit, FakeGitHub, FakeStore, FakeValidator]:
    git = FakeGit()
    api = github or FakeGitHub()
    store = FakeStore()
    validator = FakeValidator()
    subject = IntegrationCoordinator(
        repository_path=tmp_path / "repository",
        runtime_root=tmp_path / "runtime",
        remote="origin",
        git=git,
        github=api,
        store=store,
        validator=validator,
    )
    return subject, git, api, store, validator


def test_integration_pr_targets_main_from_the_feature_branch(tmp_path: Path) -> None:
    subject, _, github, store, _ = coordinator(tmp_path)

    result = subject.publish(request())

    assert result.number == 50
    assert github.creates == [request()]
    assert github.creates[0].head_ref == "feature/customer-export"
    assert github.creates[0].base_ref == "main"
    assert result.number in store.created_prs


def test_aggregate_validation_uses_a_dedicated_integration_worktree(
    tmp_path: Path,
) -> None:
    subject, git, _, _, validator = coordinator(tmp_path)
    commands = (ValidationCommand(("uv", "run", "pytest")),)

    record = subject.prepare_and_validate(
        request(),
        scope_fingerprint="aggregate-scope",
        commands=commands,
        base_commit=MAIN_HEAD,
    )

    expected = tmp_path / "runtime" / "worktrees" / "integration-p-100-d-1"
    assert git.calls == [
        (
            "prepare",
            tmp_path / "repository",
            expected,
            "feature/customer-export",
            FEATURE_HEAD,
        )
    ]
    assert validator.calls == [
        (expected, MAIN_HEAD, "aggregate-scope", commands)
    ]
    assert record.scope_fingerprint == "aggregate-scope"


def test_main_updates_use_an_ordinary_merge_then_restart_quiet_period(
    tmp_path: Path,
) -> None:
    subject, git, _, store, _ = coordinator(tmp_path)

    new_head = subject.update_from_main(request(), pr_number=41)

    assert new_head == "c" * 40
    assert [call[0] for call in git.calls] == ["prepare", "merge", "push"]
    assert git.calls[1][2:] == ("origin/main", "feature/customer-export")
    assert all(call[0] not in {"rebase", "force_push"} for call in git.calls)
    assert store.quiet_restarts == [(41, new_head)]


def test_concrete_integration_defect_creates_one_grounded_repair_child(
    tmp_path: Path,
) -> None:
    subject, _, github, _, _ = coordinator(tmp_path)
    defect = IntegrationDefect(
        finding_identity="validation:test_export_round_trip",
        title="Repair aggregate export round trip",
        reproduction="Run `uv run pytest tests/test_export.py -k round_trip`.",
        acceptance_criteria=("The aggregate round-trip test passes.",),
        source="aggregate validation",
    )
    configured_children = (101, 102)

    first = subject.ensure_repair_child(
        identity(), defect, selected_issue_numbers=configured_children
    )
    second = subject.ensure_repair_child(
        identity(), defect, selected_issue_numbers=configured_children
    )

    assert first.issue_number == 901
    assert first.effective_selected_issue_numbers == (101, 102, 901)
    assert second == first
    assert github.repair_creates == [(identity(), defect)]
    assert configured_children == (101, 102)
    assert first.parent_mode is ParentReadiness.CHILD_WORK
    assert first.origin.source == "aggregate validation"


def test_repair_child_origin_survives_restart_and_native_mode_attaches(
    tmp_path: Path,
) -> None:
    store = CoordinatorStore(tmp_path / "runner.db")
    record = IntegrationRepairChildRecord(
        "owner/project",
        100,
        "check:aggregate",
        901,
        "required check",
    )

    store.record_repair_child(record)
    restarted = CoordinatorStore(tmp_path / "runner.db")

    assert restarted.repair_child_for_finding(
        "owner/project", 100, "check:aggregate"
    ) == record
    assert restarted.repair_children("owner/project", 100) == (record,)


def test_repair_child_rejects_origin_from_another_parent(tmp_path: Path) -> None:
    subject, _, _, store, _ = coordinator(tmp_path)
    defect = IntegrationDefect(
        finding_identity="check:aggregate",
        title="Repair aggregate check",
        reproduction="Run the aggregate check.",
        acceptance_criteria=("The aggregate check passes.",),
        source="required check",
    )
    store.repair_origin = IntegrationRepairChildRecord(
        "owner/project", 999, "check:aggregate", 901, "required check"
    )

    with pytest.raises(IntegrationBlocked, match="origin does not match"):
        subject.ensure_repair_child(
            identity(), defect, selected_issue_numbers=(101,)
        )


def test_removed_scope_code_requires_human_decision_only_when_still_present() -> None:
    assert (
        removed_scope_code_blocker(
            removed_or_excluded_issue_numbers=(101,),
            issue_numbers_with_code_in_feature_branch=(),
        )
        is None
    )
    blocker = removed_scope_code_blocker(
        removed_or_excluded_issue_numbers=(101, 102),
        issue_numbers_with_code_in_feature_branch=(102, 999),
    )

    assert blocker is not None
    assert blocker.issue_numbers == (102,)
    assert "human decision" in blocker.detail


def test_external_integration_pr_is_observed_but_never_adopted(
    tmp_path: Path,
) -> None:
    external = replace(integration_pr(number=77), body="Human integration PR.")
    subject, _, github, store, _ = coordinator(
        tmp_path, github=FakeGitHub([external])
    )

    with pytest.raises(IntegrationBlocked, match="external.*not adopted"):
        subject.publish(request())

    assert github.creates == []
    assert store.created_prs == set()
def test_integration_pr_parent_ownership_survives_later_delivery() -> None:
    original = IntegrationIdentity("owner/project", 100, 1)
    body = f"Aggregate feature delivery.\n\n{integration_pr_ownership_marker(original)}"

    assert integration_pr_owned_by_parent(
        body, repository="owner/project", parent_issue=100
    )
    assert not integration_pr_owned_by_parent(
        body, repository="owner/project", parent_issue=200
    )
