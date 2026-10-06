from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from local_issue_runner.completion import ParentCompletionCheck
from local_issue_runner.models import (
    DiscoveryMode,
    IntegrationRepairChildRecord,
    ParentDeliveryRecord,
)
from local_issue_runner.scope import (
    ChildRequirement,
    DeliveryState,
    FeatureScopeState,
    allocate_delivery,
    delivery_key,
    latest_deliveries,
)
from local_issue_runner.validation import ValidationCommand, ValidationRecord


class IntegrationBlocked(RuntimeError):
    """Parent integration cannot safely proceed without external action."""


class ParentReadiness(StrEnum):
    READY = "ready"
    CHILD_WORK = "child_work"


@dataclass(frozen=True, slots=True)
class ParentMergeRequest:
    repository: str
    parent_issue: int
    delivery: int
    pr_number: int
    head_ref: str
    expected_head: str
    base_ref: str
    selected_children: tuple[int, ...]

    def __post_init__(self) -> None:
        if min(self.parent_issue, self.delivery, self.pr_number) <= 0:
            raise ValueError("parent merge identity values must be positive")
        if not all(
            (self.repository, self.head_ref, self.expected_head, self.base_ref)
        ):
            raise ValueError("parent merge refs and expected head are required")
        _unique_issue_numbers(self.selected_children, "selected child issue numbers")
        if self.head_ref == self.base_ref:
            raise ValueError("parent merge head and base refs must differ")


class ParentMergeDiscrepancy(StrEnum):
    NOT_INTEGRATED = "not_integrated"
    SELECTED_CHILDREN_CHANGED = "selected_children_changed"
    INCOMPLETE_CHILDREN = "incomplete_children"
    SCOPE_OR_FEEDBACK_CHANGED = "scope_or_feedback_changed"


def parent_merge_discrepancies(
    request: ParentMergeRequest,
    *,
    integrated: bool,
    selected_children: tuple[int, ...],
    completed_children: tuple[int, ...],
    scope_and_feedback_current: bool,
) -> tuple[ParentMergeDiscrepancy, ...]:
    _unique_issue_numbers(selected_children, "selected child issue numbers")
    _unique_issue_numbers(completed_children, "completed child issue numbers")
    completion = ParentCompletionCheck(
        integrated=integrated,
        selected_children_unchanged=selected_children == request.selected_children,
        all_selected_children_complete=set(request.selected_children).issubset(
            completed_children
        ),
        scope_and_feedback_current=scope_and_feedback_current,
    )
    discrepancies: list[ParentMergeDiscrepancy] = []
    if not completion.integrated:
        discrepancies.append(ParentMergeDiscrepancy.NOT_INTEGRATED)
    if not completion.selected_children_unchanged:
        discrepancies.append(ParentMergeDiscrepancy.SELECTED_CHILDREN_CHANGED)
    if not completion.all_selected_children_complete:
        discrepancies.append(ParentMergeDiscrepancy.INCOMPLETE_CHILDREN)
    if not completion.scope_and_feedback_current:
        discrepancies.append(ParentMergeDiscrepancy.SCOPE_OR_FEEDBACK_CHANGED)
    return tuple(discrepancies)


@dataclass(frozen=True, slots=True)
class ChildIntegrationFact:
    issue_number: int
    verifiably_complete: bool = False
    explicitly_excluded: bool = False
    outstanding_work: bool = False
    unresolved_blocking_relationship: bool = False

    def __post_init__(self) -> None:
        if self.issue_number <= 0:
            raise ValueError("child issue number must be positive")

    @property
    def resolved(self) -> bool:
        return (
            (self.verifiably_complete or self.explicitly_excluded)
            and not self.outstanding_work
            and not self.unresolved_blocking_relationship
        )


def evaluate_parent_readiness(
    *,
    selected_issue_numbers: tuple[int, ...],
    children: tuple[ChildIntegrationFact, ...],
) -> ParentReadiness:
    selected = _unique_issue_numbers(
        selected_issue_numbers, "selected child issue numbers"
    )
    facts = _child_facts_by_issue(children)
    return (
        ParentReadiness.READY
        if all(number in facts and facts[number].resolved for number in selected)
        else ParentReadiness.CHILD_WORK
    )


def _unique_issue_numbers(
    issue_numbers: tuple[int, ...], description: str
) -> tuple[int, ...]:
    if any(number <= 0 for number in issue_numbers):
        raise ValueError(f"{description} must be positive")
    if len(issue_numbers) != len(set(issue_numbers)):
        raise ValueError(f"{description} must be unique")
    return issue_numbers


def _child_facts_by_issue(
    children: tuple[ChildIntegrationFact, ...],
) -> dict[int, ChildIntegrationFact]:
    facts = {child.issue_number: child for child in children}
    if len(facts) != len(children):
        raise ValueError("child integration facts must be unique")
    return facts


@dataclass(frozen=True, slots=True)
class IntegrationIdentity:
    repository: str
    parent_issue: int
    delivery: int

    def __post_init__(self) -> None:
        if not self.repository or min(self.parent_issue, self.delivery) <= 0:
            raise ValueError("integration identity is invalid")

    @property
    def key(self) -> str:
        return f"{self.repository}:{self.parent_issue}:{self.delivery}"


@dataclass(frozen=True, slots=True)
class IntegrationRequest:
    identity: IntegrationIdentity
    feature_branch: str
    feature_head: str
    main_branch: str
    title: str
    body: str

    def __post_init__(self) -> None:
        if not all(
            (self.feature_branch, self.feature_head, self.main_branch, self.title)
        ):
            raise ValueError("integration request fields must not be empty")
        if self.feature_branch == self.main_branch:
            raise ValueError("integration head and base branches must differ")

    @property
    def head_ref(self) -> str:
        return self.feature_branch

    @property
    def base_ref(self) -> str:
        return self.main_branch


@dataclass(frozen=True, slots=True)
class IntegrationPullRequest:
    number: int
    state: str
    head_ref: str
    head_sha: str
    base_ref: str
    body: str
    merged: bool = False


def integration_pr_ownership_marker(identity: IntegrationIdentity) -> str:
    payload = json.dumps(
        {
            "repository": identity.repository,
            "parent_issue": identity.parent_issue,
            "delivery": identity.delivery,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"<!-- local-issue-runner-integration:{payload} -->"


def integration_pr_owned_by_parent(
    body: str, *, repository: str, parent_issue: int
) -> bool:
    pattern = re.compile(
        r"<!-- local-issue-runner-integration:(\{.*?\}) -->"
    )
    for match in pattern.finditer(body):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if (
            isinstance(payload, dict)
            and payload.get("repository") == repository
            and payload.get("parent_issue") == parent_issue
            and isinstance(payload.get("delivery"), int)
        ):
            return True
    return False


def parent_delivery_record(
    identity: IntegrationIdentity, selected_children: tuple[int, ...]
) -> ParentDeliveryRecord:
    return ParentDeliveryRecord(
        identity.repository,
        identity.parent_issue,
        identity.delivery,
        selected_children,
    )


@dataclass(frozen=True, slots=True)
class IntegrationDefect:
    finding_identity: str
    title: str
    reproduction: str
    acceptance_criteria: tuple[str, ...]
    source: str

    def __post_init__(self) -> None:
        if not all(
            (self.finding_identity, self.title, self.reproduction, self.source)
        ) or not self.acceptance_criteria:
            raise ValueError("integration defect requires grounded evidence")


@dataclass(frozen=True, slots=True)
class IntegrationRepairChild:
    issue_number: int
    effective_selected_issue_numbers: tuple[int, ...]
    origin: IntegrationRepairChildRecord
    parent_mode: ParentReadiness = ParentReadiness.CHILD_WORK


def _repair_child_origin(
    owner: IntegrationIdentity,
    defect: IntegrationDefect,
    issue_number: int,
) -> IntegrationRepairChildRecord:
    return IntegrationRepairChildRecord(
        owner.repository,
        owner.parent_issue,
        defect.finding_identity,
        issue_number,
        defect.source,
    )


def _validate_repair_child_origin(
    owner: IntegrationIdentity,
    defect: IntegrationDefect,
    origin: IntegrationRepairChildRecord,
) -> None:
    expected = (owner.repository, owner.parent_issue, defect.finding_identity)
    actual = (origin.repository, origin.parent_issue, origin.finding_identity)
    if actual != expected:
        raise IntegrationBlocked("repair child origin does not match its integration")


def _effective_selected_children(
    selected_issue_numbers: tuple[int, ...], repair_issue_number: int
) -> tuple[int, ...]:
    selected = _unique_issue_numbers(
        selected_issue_numbers, "selected child issue numbers"
    )
    return selected if repair_issue_number in selected else (*selected, repair_issue_number)


class IntegrationGit(Protocol):
    def prepare_integration_worktree(
        self,
        repository: Path,
        worktree: Path,
        feature_branch: str,
        expected_head: str,
    ) -> None: ...

    def merge_base_into_feature(
        self, worktree: Path, base_ref: str, feature_branch: str
    ) -> str: ...

    def push_branch(
        self, repository: Path, remote: str, branch: str, expected_head: str
    ) -> None: ...


class IntegrationGitHub(Protocol):
    def list_integration_pull_requests(
        self, repository: str, feature_branch: str, main_branch: str
    ) -> tuple[IntegrationPullRequest, ...]: ...

    def create_integration_pull_request(
        self, candidate: IntegrationRequest
    ) -> IntegrationPullRequest: ...

    def create_integration_repair_child(
        self,
        owner: IntegrationIdentity,
        defect: IntegrationDefect,
        *,
        attach_to_parent: bool,
    ) -> int: ...


class IntegrationStore(Protocol):
    def record_parent_delivery(self, record: ParentDeliveryRecord) -> None: ...

    def record_integration_pr_creation(
        self, owner: IntegrationIdentity, pull_request: IntegrationPullRequest
    ) -> None: ...

    def is_runner_created_integration_pr(
        self, owner: IntegrationIdentity, pull_request: IntegrationPullRequest
    ) -> bool: ...

    def restart_quiet_period(self, pr_number: int, revision: str) -> None: ...

    def repair_child_for_finding(
        self, repository: str, parent_issue: int, finding_identity: str
    ) -> IntegrationRepairChildRecord | None: ...

    def record_repair_child(self, record: IntegrationRepairChildRecord) -> None: ...


class AggregateValidator(Protocol):
    def validate(
        self,
        *,
        worktree: Path,
        base_commit: str,
        scope_fingerprint: str,
        commands: tuple[ValidationCommand, ...],
    ) -> ValidationRecord: ...


class IntegrationCoordinator:
    def __init__(
        self,
        *,
        repository_path: Path,
        runtime_root: Path,
        remote: str,
        git: IntegrationGit,
        github: IntegrationGitHub,
        store: IntegrationStore,
        validator: AggregateValidator,
    ) -> None:
        self.repository_path = repository_path
        self.runtime_root = runtime_root.resolve()
        self.remote = remote
        self.git = git
        self.github = github
        self.store = store
        self.validator = validator

    def _worktree(self, owner: IntegrationIdentity) -> Path:
        path = (
            self.runtime_root
            / "worktrees"
            / f"integration-p-{owner.parent_issue}-d-{owner.delivery}"
        )
        if not path.resolve().is_relative_to(self.runtime_root / "worktrees"):
            raise IntegrationBlocked("integration worktree escapes the runtime root")
        return path

    def _prepare(self, request: IntegrationRequest) -> Path:
        worktree = self._worktree(request.identity)
        self.git.prepare_integration_worktree(
            self.repository_path,
            worktree,
            request.feature_branch,
            request.feature_head,
        )
        return worktree

    def record_delivery(
        self,
        owner: IntegrationIdentity,
        selected_issue_numbers: tuple[int, ...],
    ) -> ParentDeliveryRecord:
        record = parent_delivery_record(owner, selected_issue_numbers)
        self.store.record_parent_delivery(record)
        return record

    def publish(self, request: IntegrationRequest) -> IntegrationPullRequest:
        candidates = self.github.list_integration_pull_requests(
            request.identity.repository,
            request.feature_branch,
            request.main_branch,
        )
        owned = tuple(
            candidate
            for candidate in candidates
            if self.store.is_runner_created_integration_pr(
                request.identity, candidate
            )
        )
        if len(owned) > 1:
            raise IntegrationBlocked("multiple runner-owned integration PRs found")
        if owned:
            candidate = owned[0]
            self._assert_request_matches(request, candidate)
            return candidate
        if candidates:
            raise IntegrationBlocked(
                "external integration PR observed and not adopted"
            )
        created = self.github.create_integration_pull_request(request)
        self._assert_request_matches(request, created)
        self.store.record_integration_pr_creation(request.identity, created)
        return created

    @staticmethod
    def _assert_request_matches(
        request: IntegrationRequest, candidate: IntegrationPullRequest
    ) -> None:
        if candidate.head_ref != request.feature_branch:
            raise IntegrationBlocked("integration PR head branch does not match")
        if candidate.head_sha != request.feature_head:
            raise IntegrationBlocked("integration PR head commit does not match")
        if candidate.base_ref != request.main_branch:
            raise IntegrationBlocked("integration PR base branch does not match")

    def prepare_and_validate(
        self,
        request: IntegrationRequest,
        *,
        scope_fingerprint: str,
        commands: tuple[ValidationCommand, ...],
        base_commit: str,
    ) -> ValidationRecord:
        return self.validator.validate(
            worktree=self._prepare(request),
            base_commit=base_commit,
            scope_fingerprint=scope_fingerprint,
            commands=commands,
        )

    def update_from_main(
        self, request: IntegrationRequest, *, pr_number: int
    ) -> str:
        worktree = self._prepare(request)
        new_head = self.git.merge_base_into_feature(
            worktree, f"{self.remote}/{request.main_branch}", request.feature_branch
        )
        self.git.push_branch(
            self.repository_path,
            self.remote,
            request.feature_branch,
            new_head,
        )
        self.store.restart_quiet_period(pr_number, new_head)
        return new_head

    def ensure_repair_child(
        self,
        owner: IntegrationIdentity,
        defect: IntegrationDefect,
        *,
        selected_issue_numbers: tuple[int, ...],
        discovery_mode: DiscoveryMode = DiscoveryMode.EXPLICIT,
    ) -> IntegrationRepairChild:
        origin = self.store.repair_child_for_finding(
            owner.repository, owner.parent_issue, defect.finding_identity
        )
        if origin is None:
            issue_number = self.github.create_integration_repair_child(
                owner,
                defect,
                attach_to_parent=discovery_mode is DiscoveryMode.NATIVE,
            )
            origin = _repair_child_origin(owner, defect, issue_number)
            self.store.record_repair_child(origin)
        _validate_repair_child_origin(owner, defect, origin)
        selected = _effective_selected_children(
            selected_issue_numbers, origin.issue_number
        )
        return IntegrationRepairChild(origin.issue_number, selected, origin)


@dataclass(frozen=True, slots=True)
class IntegrationBlocker:
    issue_numbers: tuple[int, ...]
    detail: str


def removed_scope_code_blocker(
    *,
    removed_or_excluded_issue_numbers: tuple[int, ...],
    issue_numbers_with_code_in_feature_branch: tuple[int, ...],
) -> IntegrationBlocker | None:
    affected = tuple(
        sorted(
            set(removed_or_excluded_issue_numbers)
            & set(issue_numbers_with_code_in_feature_branch)
        )
    )
    if not affected:
        return None
    return IntegrationBlocker(
        affected,
        "removed or excluded child code is still present in the feature branch; "
        "pause for a human decision because it may remain or be reverted only "
        "through explicit work",
    )


@dataclass(frozen=True, slots=True)
class ReopenedWorkPlan:
    new_deliveries: tuple[DeliveryState, ...]
    paused_delivery_keys: tuple[str, ...]
    reopen_parent: bool
    parent_delivery: int
    integration_ready: bool
    integration_blocker: IntegrationBlocker | None = None


def reconcile_reopened_work(
    previous: FeatureScopeState,
    *,
    requirements: tuple[ChildRequirement, ...],
    issue_states: Mapping[int, str],
    excluded_issue_numbers: tuple[int, ...] = (),
    issue_numbers_with_code_in_feature_branch: tuple[int, ...] | None = None,
) -> ReopenedWorkPlan:
    latest = latest_deliveries(previous.deliveries)
    current = {item.issue_number: item for item in requirements}
    reopened = {
        issue
        for issue, item in latest.items()
        if item.merged
        and item.issue_state.upper() == "CLOSED"
        and issue_states.get(issue, item.issue_state).upper() == "OPEN"
        and issue in current
    }
    fresh = tuple(
        allocate_delivery(previous, current[issue], latest[issue].delivery + 1)
        for issue in sorted(reopened)
    )
    paused = tuple(
        delivery_key(previous, item)
        for item in previous.deliveries
        if not item.merged and set(item.prerequisites) & reopened
    )
    removed = tuple(
        sorted(
            ({item.issue_number for item in previous.requirements} - set(current))
            | set(excluded_issue_numbers)
        )
    )
    blocker = removed_scope_code_blocker(
        removed_or_excluded_issue_numbers=removed,
        issue_numbers_with_code_in_feature_branch=(
            removed
            if issue_numbers_with_code_in_feature_branch is None
            else issue_numbers_with_code_in_feature_branch
        ),
    )
    reopen_parent = bool(reopened and previous.integration.delivered)
    return ReopenedWorkPlan(
        new_deliveries=fresh,
        paused_delivery_keys=paused,
        reopen_parent=reopen_parent,
        parent_delivery=(
            previous.integration.delivery + 1
            if reopen_parent
            else previous.integration.delivery
        ),
        integration_ready=previous.integration.ready and not reopened and not removed,
        integration_blocker=blocker,
    )
