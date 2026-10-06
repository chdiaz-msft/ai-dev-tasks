from __future__ import annotations

import hashlib
import signal
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, Protocol

from local_issue_runner.agent_backend import (
    CopilotResult,
    DeliveryAgent,
    WorktreeJobRegistry,
)
from local_issue_runner.checks import (
    CheckDecision,
    CheckObservation,
    CIRepairPolicy,
    FailureObservation,
    RepairAction,
    RepairDecision,
)
from local_issue_runner.cleanup import (
    FeatureRetentionRequest,
    verify_feature_retention,
)
from local_issue_runner.completion import (
    ChildCompletionService,
    CompletionBlocked,
    CompletionDelivery,
    CompletionResult,
)
from local_issue_runner.config import load_config
from local_issue_runner.db import (
    ChildPRStore,
    CoordinatorStore,
    FeedbackStore,
    SnapshotStore,
)
from local_issue_runner.feedback import FeedbackHandler, RepairPort, ReplyPort
from local_issue_runner.git_ops import (
    DeliveryIdentity,
    DeliveryWorkspace,
    PreparedDelivery,
)
from local_issue_runner.github_api import (
    ChildPullRequest,
    child_pr_ownership_marker,
)
from local_issue_runner.graph import FactReader, build_feature_snapshots
from local_issue_runner.integration import (
    IntegrationBlocker,
    ParentMergeRequest,
    parent_merge_discrepancies,
    reconcile_reopened_work,
)
from local_issue_runner.merge_policy import (
    MergeExecutionBlocked,
    MergeReadinessDecision,
    MergeReadinessFacts,
    MergeReadinessPolicy,
    MergeTarget,
    require_merge_authorization,
)
from local_issue_runner.models import (
    ActionIntent,
    ActionState,
    ChildPRIdentity,
    ChildPRRequest,
    ChildPRResult,
    ChildPRState,
    ControlScope,
    FeatureSnapshot,
    FeedbackDecision,
    FeedbackItem,
    FeedbackReconcileResult,
    IntegrationRepairChildRecord,
    NextTransition,
    ParentDeliveryRecord,
    PlanReport,
    ProcessIdentity,
    TransitionKind,
)
from local_issue_runner.qualify import QualificationService
from local_issue_runner.scope import (
    ChildRequirement,
    ChildScopeInput,
    DeliveryState,
    FeatureScopeState,
    ScopeManifest,
    build_child_scope_manifest,
    delivery_key,
    latest_deliveries,
)


@dataclass(frozen=True, slots=True)
class PlanServices:
    github: FactReader
    snapshots: SnapshotStore
    git: object
    agent: object
    validation: object
    repair_children: Callable[
        [str, int], tuple[IntegrationRepairChildRecord, ...]
    ] | None = None
    completion_satisfied: Callable[[str, int, int, str], bool] | None = None


class Planner(Protocol):
    def select(self) -> Any | None: ...


class Supervisor(Protocol):
    def launch(self, transition: Any) -> Any: ...
    def wait(self, job: Any) -> None: ...
    def request_graceful_cancellation(self, job: Any) -> None: ...
    def terminate_owned_tree(self, job: Any) -> None: ...


@dataclass(frozen=True, slots=True)
class CoordinatorServices:
    planner: Planner
    supervisor: Supervisor


@dataclass(frozen=True, slots=True)
class CoordinatorStatus:
    paused: bool
    active_jobs: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class ObservedTransition:
    transition: object | None
    result: str


def reconcile_child_completions(
    deliveries: tuple[CompletionDelivery, ...],
    completion: ChildCompletionService,
) -> tuple[CompletionResult, ...]:
    """Observe durable merge evidence before a later graph reconciliation."""
    return tuple(completion.reconcile(delivery) for delivery in deliveries)


class FeedbackReader(Protocol):
    def list_feedback(self, pr_number: int) -> tuple[FeedbackItem, ...]: ...


class FeedbackGitHub(FeedbackReader, ReplyPort, Protocol):
    pass


class OwnedPRFeedbackReconciler:
    """Refresh and account for every feedback surface on one managed PR."""

    def __init__(
        self,
        *,
        store: FeedbackStore,
        github: FeedbackGitHub,
        repairs: RepairPort,
        runner_login: str,
    ) -> None:
        self._github = github
        self._handler = FeedbackHandler(
            store=store,
            replies=github,
            repairs=repairs,
            runner_login=runner_login,
        )

    def reconcile(
        self,
        pr_number: int,
        *,
        decide: Callable[[FeedbackItem], FeedbackDecision],
    ) -> FeedbackReconcileResult:
        return self._handler.reconcile(
            pr_number,
            self._github.list_feedback(pr_number),
            decide=decide,
        )


class CICheckReader(Protocol):
    def checks_for_head(self, head_sha: str) -> tuple[CheckObservation, ...]: ...


class MergeReadinessFactReader(Protocol):
    """Read-only port that must refresh authoritative facts on every call."""

    def refresh_merge_readiness(
        self, target: MergeTarget
    ) -> MergeReadinessFacts: ...


def reconcile_merge_readiness(
    target: MergeTarget,
    *,
    facts: MergeReadinessFactReader,
    policy: MergeReadinessPolicy | None = None,
) -> MergeReadinessDecision:
    """Refresh and evaluate gates without selecting or executing a merge."""
    current = facts.refresh_merge_readiness(target)
    if current.target is not target:
        raise ValueError("refreshed merge facts do not match the requested target")
    return (policy or MergeReadinessPolicy()).evaluate(current)


class ChildMergeStatus(StrEnum):
    BLOCKED_QUALIFICATION = "blocked_qualification"
    BLOCKED_OWNERSHIP = "blocked_ownership"
    BLOCKED_READINESS = "blocked_readiness"
    MERGED = "merged"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class ChildMergeRequest:
    repository: str
    parent_issue: int
    issue_number: int
    delivery: int
    pr_number: int
    head_ref: str
    expected_head: str
    base_ref: str

    def __post_init__(self) -> None:
        if min(self.parent_issue, self.issue_number, self.delivery, self.pr_number) <= 0:
            raise ValueError("child merge identity values must be positive")
        if not all(
            (self.repository, self.head_ref, self.expected_head, self.base_ref)
        ):
            raise ValueError("child merge refs and expected head are required")


@dataclass(frozen=True, slots=True)
class ChildMergeResult:
    status: ChildMergeStatus
    resulting_commit: str | None = None


class ChildMergeStore(Protocol):
    def runner_owns_pull_request(self, request: ChildMergeRequest) -> bool: ...
    def record_merge_intent(self, request: ChildMergeRequest) -> None: ...
    def record_merge_result(
        self, request: ChildMergeRequest, resulting_commit: str
    ) -> None: ...
    def recorded_merge(self, request: ChildMergeRequest) -> str | None: ...


class ChildMergeGitHub(Protocol):
    def observe_child_merge(
        self, repository: str, pr_number: int
    ) -> tuple[bool, str, str | None]: ...
    def squash_merge_child(
        self, repository: str, pr_number: int, expected_head: str
    ) -> str: ...


class ChildMergeCompletion(Protocol):
    def close_verified_child(self, request: ChildMergeRequest) -> None: ...


class ChildAutomergeService:
    def __init__(
        self,
        *,
        store: ChildMergeStore,
        github: ChildMergeGitHub,
        completion: ChildMergeCompletion,
        qualification: QualificationService,
    ) -> None:
        self.store = store
        self.github = github
        self.completion = completion
        self.qualification = qualification

    def reconcile(
        self,
        request: ChildMergeRequest,
        *,
        readiness: MergeReadinessDecision,
    ) -> ChildMergeResult:
        if not self.qualification.child_merge_valid:
            return ChildMergeResult(ChildMergeStatus.BLOCKED_QUALIFICATION)
        try:
            require_merge_authorization(readiness, MergeTarget.CHILD)
        except MergeExecutionBlocked:
            return ChildMergeResult(ChildMergeStatus.BLOCKED_READINESS)
        if not self.store.runner_owns_pull_request(request):
            return ChildMergeResult(ChildMergeStatus.BLOCKED_OWNERSHIP)

        merged, observed_head, resulting_commit = self.github.observe_child_merge(
            request.repository, request.pr_number
        )
        if observed_head != request.expected_head:
            raise MergeExecutionBlocked(
                "pull request head no longer matches the expected head"
            )
        if merged:
            if not resulting_commit:
                raise MergeExecutionBlocked(
                    "merged pull request has no resulting commit"
                )
            return self._complete_merge(
                request,
                status=ChildMergeStatus.COMPLETED,
                resulting_commit=resulting_commit,
            )

        recorded = self.store.recorded_merge(request)
        if recorded is not None:
            raise MergeExecutionBlocked(
                "local merge result exists but the pull request is not merged"
            )
        self.store.record_merge_intent(request)
        resulting_commit = self.github.squash_merge_child(
            request.repository, request.pr_number, request.expected_head
        )
        if not resulting_commit:
            raise MergeExecutionBlocked("squash merge returned no resulting commit")
        return self._complete_merge(
            request,
            status=ChildMergeStatus.MERGED,
            resulting_commit=resulting_commit,
        )

    def _complete_merge(
        self,
        request: ChildMergeRequest,
        *,
        status: ChildMergeStatus,
        resulting_commit: str,
    ) -> ChildMergeResult:
        self.store.record_merge_result(request, resulting_commit)
        self.completion.close_verified_child(request)
        return ChildMergeResult(status, resulting_commit)


class ParentMergeStatus(StrEnum):
    BLOCKED_QUALIFICATION = "blocked_qualification"
    BLOCKED_OWNERSHIP = "blocked_ownership"
    BLOCKED_READINESS = "blocked_readiness"
    MERGED_PARENT_OPEN = "merged_parent_open"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class ParentMergeResult:
    status: ParentMergeStatus
    resulting_commit: str | None = None


class ParentMergeStore(Protocol):
    def runner_owns_pull_request(self, candidate: ParentMergeRequest) -> bool: ...
    def record_parent_merge_intent(self, candidate: ParentMergeRequest) -> None: ...
    def record_parent_merge_result(
        self, candidate: ParentMergeRequest, resulting_commit: str
    ) -> None: ...
    def recorded_parent_merge(self, candidate: ParentMergeRequest) -> str | None: ...


class ParentMergeGitHub(Protocol):
    def observe_parent_merge(
        self, repository: str, pr_number: int
    ) -> tuple[bool, str, str | None]: ...
    def merge_parent(
        self,
        repository: str,
        pr_number: int,
        expected_head: str,
        *,
        method: str,
    ) -> str: ...
    def close_parent(self, repository: str, parent_issue: int) -> None: ...


class ParentMergeFacts(Protocol):
    def merged_into_main(
        self, candidate: ParentMergeRequest, resulting_commit: str
    ) -> bool: ...
    def current_selected_children(
        self, candidate: ParentMergeRequest
    ) -> tuple[int, ...]: ...
    def completed_children(self, candidate: ParentMergeRequest) -> tuple[int, ...]: ...
    def scope_and_feedback_current(self, candidate: ParentMergeRequest) -> bool: ...


class ParentAutomergeService:
    def __init__(
        self,
        *,
        store: ParentMergeStore,
        github: ParentMergeGitHub,
        facts: ParentMergeFacts,
        qualification: QualificationService,
    ) -> None:
        self.store = store
        self.github = github
        self.facts = facts
        self.qualification = qualification

    def reconcile(
        self,
        request: ParentMergeRequest,
        *,
        readiness: MergeReadinessDecision,
    ) -> ParentMergeResult:
        if not self.qualification.parent_merge_valid:
            return ParentMergeResult(ParentMergeStatus.BLOCKED_QUALIFICATION)
        try:
            require_merge_authorization(readiness, MergeTarget.PARENT)
        except MergeExecutionBlocked:
            return ParentMergeResult(ParentMergeStatus.BLOCKED_READINESS)
        if not self.store.runner_owns_pull_request(request):
            return ParentMergeResult(ParentMergeStatus.BLOCKED_OWNERSHIP)

        merged, observed_head, resulting_commit = self.github.observe_parent_merge(
            request.repository, request.pr_number
        )
        if observed_head != request.expected_head:
            raise MergeExecutionBlocked(
                "pull request head no longer matches the expected head"
            )
        if merged:
            if not resulting_commit:
                raise MergeExecutionBlocked(
                    "merged parent pull request has no resulting commit"
                )
        else:
            if self.store.recorded_parent_merge(request) is not None:
                raise MergeExecutionBlocked(
                    "local parent merge result exists but the pull request is not merged"
                )
            self.store.record_parent_merge_intent(request)
            resulting_commit = self.github.merge_parent(
                request.repository,
                request.pr_number,
                request.expected_head,
                method="merge",
            )
            if not resulting_commit:
                raise MergeExecutionBlocked(
                    "parent merge returned no resulting commit"
                )

        self.store.record_parent_merge_result(request, resulting_commit)
        discrepancies = parent_merge_discrepancies(
            request,
            integrated=self.facts.merged_into_main(request, resulting_commit),
            selected_children=self.facts.current_selected_children(request),
            completed_children=self.facts.completed_children(request),
            scope_and_feedback_current=self.facts.scope_and_feedback_current(request),
        )
        if discrepancies:
            return ParentMergeResult(
                ParentMergeStatus.MERGED_PARENT_OPEN, resulting_commit
            )
        self.github.close_parent(request.repository, request.parent_issue)
        return ParentMergeResult(ParentMergeStatus.COMPLETED, resulting_commit)


@dataclass(frozen=True, slots=True)
class FeatureFinalizationRequest:
    repository: str
    parent_issue: int
    delivery: int
    feature_branch: Path
    retained_paths: tuple[Path, ...]

    def __post_init__(self) -> None:
        if not self.repository or min(self.parent_issue, self.delivery) <= 0:
            raise ValueError("feature finalization identity is invalid")


class FeatureFinalizationStore(Protocol):
    def parent_deliveries(
        self, repository: str, parent_issue: int
    ) -> tuple[ParentDeliveryRecord, ...]: ...


def finalize_feature(
    request: FeatureFinalizationRequest, *, store: FeatureFinalizationStore
) -> None:
    deliveries = store.parent_deliveries(request.repository, request.parent_issue)
    if not any(delivery.delivery == request.delivery for delivery in deliveries):
        raise CompletionBlocked(
            "feature cannot be finalized without matching parent delivery evidence"
        )
    verify_feature_retention(
        FeatureRetentionRequest(request.feature_branch, request.retained_paths)
    )


class RepairDispatcher(Protocol):
    def start_repair(self, failure: FailureObservation) -> None: ...


class OwnedPRCIReconciler:
    """Refresh both check-run and status-context observations for one PR head."""

    def __init__(
        self,
        *,
        checks: CICheckReader,
        policy: CIRepairPolicy,
        repairs: RepairDispatcher,
    ) -> None:
        self._checks = checks
        self._policy = policy
        self._repairs = repairs

    def reconcile_checks(self, head_sha: str) -> CheckDecision:
        return self._policy.evaluate_checks(
            head_sha=head_sha, checks=self._checks.checks_for_head(head_sha)
        )

    def reconcile_failure(
        self, failure: FailureObservation, *, infrastructure_attempt: int = 1
    ) -> RepairDecision:
        decision = self._policy.plan_failure(
            failure, infrastructure_attempt=infrastructure_attempt
        )
        if decision.action is RepairAction.START_REPAIR:
            self._repairs.start_repair(failure)
        return decision


class RecoveryBlocked(RuntimeError):
    pass


class PublicationBlocked(RuntimeError):
    pass


class ChildPRGit(Protocol):
    def remote_branch_head(
        self, repository: str, remote: str, branch: str
    ) -> str | None: ...

    def push_branch(
        self, repository: str, remote: str, branch: str, expected_head: str
    ) -> None: ...


class ChildPRGitHub(Protocol):
    def list_child_pull_requests(
        self, repository: str, issue_number: int
    ) -> tuple[ChildPullRequest, ...]: ...

    def create_child_pull_request(
        self, candidate: ChildPRRequest
    ) -> ChildPullRequest: ...


class ChildPRPublisher:
    _CLOSED_UNMERGED_DETAIL = (
        "runner PR was closed unmerged; explicit retry or a new delivery is required"
    )
    _RECOVERABLE_ACTION_STATES = frozenset(
        {ActionState.PENDING, ActionState.RUNNING}
    )

    def __init__(
        self,
        *,
        store: ChildPRStore,
        git: ChildPRGit,
        github: ChildPRGitHub,
        remote: str,
    ) -> None:
        self.store = store
        self.git = git
        self.github = github
        self.remote = remote

    @staticmethod
    def _intent_key(kind: str, request: ChildPRRequest) -> str:
        material = (
            f"{request.identity.key}\0{request.head_ref}\0{request.head_sha}\0"
            f"{request.base_ref}"
        ).encode()
        return f"{request.identity.repository}:{kind}:{hashlib.sha256(material).hexdigest()}"

    def push_intent_key(self, request: ChildPRRequest) -> str:
        return self._intent_key("push", request)

    def creation_intent_key(self, request: ChildPRRequest) -> str:
        return self._intent_key("create-pr", request)

    def _finish_recovered_action(self, key: str) -> None:
        if self.store.action_state(key) in self._RECOVERABLE_ACTION_STATES:
            self.store.record_action_outcome(key, ActionState.SUCCEEDED)

    def _start_action(self, key: str, state: ActionState) -> bool:
        if state is ActionState.SUCCEEDED:
            return False
        self.store.mark_action_running(key)
        return True

    def _recover_pr_creation(
        self,
        request: ChildPRRequest,
        key: str,
        marker: str,
        candidate: ChildPullRequest,
    ) -> None:
        self.store.record_runner_pr_creation(
            identity=request.identity,
            intent_key=key,
            pr_number=candidate.number,
            head_ref=candidate.head_ref,
            head_sha=candidate.head_sha,
            base_ref=candidate.base_ref,
            marker=marker,
        )
        self._finish_recovered_action(key)

    @staticmethod
    def _require_exact(request: ChildPRRequest, pr: ChildPullRequest) -> None:
        if pr.head_ref != request.head_ref or pr.head_sha != request.head_sha:
            raise PublicationBlocked("pull request head does not match the delivery")
        if pr.base_ref != request.base_ref:
            raise PublicationBlocked("pull request base does not match the delivery")

    def reconcile(self, request: ChildPRRequest) -> ChildPRResult:
        abandoned = self.store.abandonment(request.identity)
        if abandoned is not None:
            return ChildPRResult(ChildPRState.ABANDONED, abandoned[0], abandoned[1])

        remote_head = self.git.remote_branch_head(
            request.identity.repository, self.remote, request.head_ref
        )
        if remote_head is not None and remote_head != request.head_sha:
            raise PublicationBlocked("remote head does not match the validated commit")

        candidates = self.github.list_child_pull_requests(
            request.identity.repository, request.identity.issue_number
        )
        create_key = self.creation_intent_key(request)
        marker = child_pr_ownership_marker(request.identity)
        create_state = self.store.action_state(create_key)
        ignored: set[int] = set()
        classified: list[tuple[ChildPullRequest, bool, bool]] = []
        for candidate in candidates:
            owned = self.store.runner_created_pr(request.identity, candidate)
            recovering = (
                create_state in self._RECOVERABLE_ACTION_STATES
                and marker in candidate.body
            )
            if (
                owned
                and candidate.state == "CLOSED"
                and not candidate.merged
                and self.store.abandonment_was_retried(request.identity)
            ):
                ignored.add(candidate.number)
                continue
            classified.append((candidate, owned, recovering))

        if len(classified) > 1:
            numbers = ", ".join(f"#{candidate.number}" for candidate, _, _ in classified)
            return ChildPRResult(
                ChildPRState.BLOCKED_AMBIGUOUS,
                detail=(
                    f"multiple pull requests plausibly represent this delivery: {numbers}; "
                    "operator review or a new delivery is required"
                ),
            )

        if classified:
            candidate, owned, recovering = classified[0]
            self._require_exact(request, candidate)
            if owned or recovering:
                if recovering and not owned:
                    self._recover_pr_creation(
                        request, create_key, marker, candidate
                    )
                if candidate.state == "CLOSED" and not candidate.merged:
                    self.store.record_abandonment(
                        request.identity,
                        candidate.number,
                        self._CLOSED_UNMERGED_DETAIL,
                    )
                    return ChildPRResult(
                        ChildPRState.ABANDONED, candidate.number,
                        self._CLOSED_UNMERGED_DETAIL,
                    )
                return ChildPRResult(ChildPRState.OPEN, candidate.number)
            return ChildPRResult(
                ChildPRState.BLOCKED_EXTERNAL,
                candidate.number,
                "matching work is represented by a PR not created by this runner; "
                "waiting for external resolution or a new delivery",
            )

        push_key = self.push_intent_key(request)
        if remote_head == request.head_sha:
            self._finish_recovered_action(push_key)
            return ChildPRResult(ChildPRState.READY_TO_CREATE)
        return ChildPRResult(ChildPRState.READY_TO_PUSH)

    def publish(self, request: ChildPRRequest) -> ChildPRResult:
        result = self.reconcile(request)
        if result.state is ChildPRState.READY_TO_PUSH:
            key = self.push_intent_key(request)
            state = self.store.record_push_intent(request, key)
            if self._start_action(key, state):
                self.git.push_branch(
                    request.identity.repository, self.remote,
                    request.head_ref, request.head_sha,
                )
            result = self.reconcile(request)
        if result.state is not ChildPRState.READY_TO_CREATE:
            return result

        key = self.creation_intent_key(request)
        state = self.store.record_pr_creation_intent(request, key)
        if not self._start_action(key, state):
            return self.reconcile(request)
        marker = child_pr_ownership_marker(request.identity)
        body = request.body.rstrip()
        candidate = ChildPRRequest(
            identity=request.identity,
            head_ref=request.head_ref,
            head_sha=request.head_sha,
            base_ref=request.base_ref,
            title=request.title,
            body=f"{body}\n\n{marker}" if body else marker,
        )
        created = self.github.create_child_pull_request(candidate)
        self._require_exact(request, created)
        self.store.record_runner_pr_creation(
            identity=request.identity,
            intent_key=key,
            pr_number=created.number,
            head_ref=created.head_ref,
            head_sha=created.head_sha,
            base_ref=created.base_ref,
            marker=marker,
        )
        self.store.record_action_outcome(key, ActionState.SUCCEEDED)
        return ChildPRResult(ChildPRState.OPEN, created.number)

    def retry(self, identity: ChildPRIdentity) -> None:
        self.store.clear_abandonment(identity)


@dataclass(frozen=True, slots=True)
class LocalDeliveryResult:
    workspace: PreparedDelivery
    scope: ScopeManifest
    agent_result: CopilotResult


class ScopeActionKind(str, Enum):
    CANCEL_STALE_JOB = "cancel_stale_job"
    RESTART_WITH_LATEST_SCOPE = "restart_with_latest_scope"
    START_FRESH_DELIVERY = "start_fresh_delivery"
    REOPEN_PARENT = "reopen_parent"
    ALLOCATE_PARENT_DELIVERY = "allocate_parent_delivery"
    REQUIRE_SCOPE_DECISION = "require_scope_decision"


@dataclass(frozen=True, slots=True)
class ScopeReconciliationPlan:
    cancelled_delivery_keys: tuple[str, ...] = ()
    restart_issue_numbers: tuple[int, ...] = ()
    new_delivery_issue_numbers: tuple[int, ...] = ()
    new_deliveries: tuple[DeliveryState, ...] = ()
    paused_delivery_keys: tuple[str, ...] = ()
    reopen_parent: bool = False
    parent_delivery: int = 1
    integration_ready: bool = False
    integration_blocker: IntegrationBlocker | None = None
    scope_decision_issue_numbers: tuple[int, ...] = ()

    def actions_for(self, issue_number: int) -> tuple[ScopeActionKind, ...]:
        actions: list[ScopeActionKind] = []
        if issue_number in self.restart_issue_numbers:
            actions.extend(
                (
                    ScopeActionKind.CANCEL_STALE_JOB,
                    ScopeActionKind.RESTART_WITH_LATEST_SCOPE,
                )
            )
        if any(item.issue_number == issue_number for item in self.new_deliveries):
            actions.append(ScopeActionKind.START_FRESH_DELIVERY)
        if issue_number in self.scope_decision_issue_numbers:
            actions.append(ScopeActionKind.REQUIRE_SCOPE_DECISION)
        return tuple(actions)

    def actions_for_parent(self) -> tuple[ScopeActionKind, ...]:
        if not self.reopen_parent:
            return ()
        return (
            ScopeActionKind.REOPEN_PARENT,
            ScopeActionKind.ALLOCATE_PARENT_DELIVERY,
        )


def reconcile_scope(
    previous: FeatureScopeState,
    *,
    requirements: tuple[ChildRequirement, ...],
    issue_states: Mapping[int, str],
    excluded_issue_numbers: tuple[int, ...] = (),
) -> ScopeReconciliationPlan:
    """Diff child-local requirement fingerprints without invalidating siblings."""
    current = {item.issue_number: item for item in requirements}
    prior = {item.issue_number: item for item in previous.requirements}
    latest_delivery = latest_deliveries(previous.deliveries)

    cancelled: list[str] = []
    restarts: list[int] = []
    for issue_number in sorted(current.keys() & prior.keys()):
        if current[issue_number].fingerprint == prior[issue_number].fingerprint:
            continue
        delivery = latest_delivery.get(issue_number)
        if delivery is None or delivery.merged or not delivery.job_running:
            continue
        cancelled.append(delivery_key(previous, delivery))
        restarts.append(issue_number)

    reopened = reconcile_reopened_work(
        previous,
        requirements=requirements,
        issue_states=issue_states,
        excluded_issue_numbers=excluded_issue_numbers,
    )
    return ScopeReconciliationPlan(
        cancelled_delivery_keys=tuple(cancelled),
        restart_issue_numbers=tuple(restarts),
        new_delivery_issue_numbers=tuple(sorted(current.keys() - prior.keys())),
        new_deliveries=reopened.new_deliveries,
        paused_delivery_keys=reopened.paused_delivery_keys,
        reopen_parent=reopened.reopen_parent,
        parent_delivery=reopened.parent_delivery,
        integration_ready=reopened.integration_ready,
        integration_blocker=reopened.integration_blocker,
        scope_decision_issue_numbers=(
            ()
            if reopened.integration_blocker is None
            else reopened.integration_blocker.issue_numbers
        ),
    )


def deliver_child_locally(
    *,
    repository: Path,
    remote: str,
    feature_branch: str,
    scope_input: ChildScopeInput,
    workspace: DeliveryWorkspace,
    agent: DeliveryAgent,
) -> LocalDeliveryResult:
    identity = DeliveryIdentity(
        scope_input.parent_issue,
        scope_input.issue_number,
        scope_input.delivery,
    )
    prepared = workspace.prepare(
        repository=repository,
        remote=remote,
        feature_branch=feature_branch,
        identity=identity,
    )
    scope = build_child_scope_manifest(scope_input)
    scope.persist(
        workspace.runtime_root / "scopes" / f"{identity.slug}.json"
    )
    delivery_key = (
        f"{scope_input.repository}:{scope_input.parent_issue}:"
        f"{scope_input.issue_number}:{scope_input.delivery}"
    )
    result = agent.run_once(delivery_key, prepared.worktree, scope.canonical_json)
    return LocalDeliveryResult(prepared, scope, result)


class InterruptController:
    def __init__(self, supervisor: Supervisor) -> None:
        self._supervisor = supervisor
        self._jobs: list[object] = []
        self._interrupts = 0
        self._lock = threading.Lock()

    @property
    def dispatch_allowed(self) -> bool:
        return self._interrupts == 0

    @property
    def jobs(self) -> tuple[object, ...]:
        with self._lock:
            return tuple(self._jobs)

    def track(self, job: object) -> None:
        with self._lock:
            self._jobs.append(job)

    def untrack(self, job: object) -> None:
        with self._lock:
            if job in self._jobs:
                self._jobs.remove(job)

    def interrupt(self) -> None:
        self._interrupts += 1
        operation = (
            self._supervisor.request_graceful_cancellation
            if self._interrupts == 1
            else self._supervisor.terminate_owned_tree
        )
        for job in self.jobs:
            operation(job)


def _default_process_probe(identity: ProcessIdentity) -> bool | None:
    try:
        import os

        os.kill(identity.pid, 0)
    except ProcessLookupError:
        return False
    except (OSError, PermissionError):
        return None
    return True


class Coordinator:
    def __init__(
        self,
        store: CoordinatorStore,
        services: CoordinatorServices,
        *,
        recover_intent: Callable[[ActionIntent], ActionState] | None = None,
        process_probe: Callable[[ProcessIdentity], bool | None] = _default_process_probe,
        poll_seconds: float = 300,
        emit: Callable[[str], None] = print,
        worktree_jobs: WorktreeJobRegistry | None = None,
    ) -> None:
        self.store = store
        self.services = services
        self._recover_intent = recover_intent
        self._process_probe = process_probe
        self._poll_seconds = poll_seconds
        self._emit = emit
        self.worktree_jobs = worktree_jobs or WorktreeJobRegistry()
        self._job_completed = threading.Event()
        self._job_errors: list[Exception] = []
        self._background_dispatch = False
        self.interrupts = InterruptController(services.supervisor)

    def status(self) -> CoordinatorStatus:
        return CoordinatorStatus(
            paused=self.store.pause_state(ControlScope()).paused,
            active_jobs=self.interrupts.jobs,
        )

    def _ensure_worktrees_recoverable(self) -> None:
        for process in self.store.unresolved_processes():
            if self.worktree_jobs.is_running(process.worktree):
                continue
            if self._process_probe(process.identity) is not False:
                raise RecoveryBlocked(
                    f"worktree {process.worktree} may still be in use by process "
                    f"{process.identity.pid}; refusing replacement"
                )

    def _recover_action_intents(self) -> None:
        intents = self.store.unfinished_action_intents()
        recover_intent = self._recover_intent
        if intents and recover_intent is None:
            raise RecoveryBlocked(
                f"{len(intents)} interrupted action intent(s) require reconciliation"
            )
        for intent in intents:
            if recover_intent is None:
                raise AssertionError("recovery callback required for unfinished intents")
            state = recover_intent(intent)
            if not state.terminal:
                raise RecoveryBlocked(
                    f"recovery for {intent.key} did not produce a terminal result"
                )
            self.store.record_action_outcome(intent.key, state)
            self._emit(f"recover action={intent.kind} result={state.value}")

    def _recover(self) -> None:
        self._ensure_worktrees_recoverable()
        self._recover_action_intents()

    def run(self, *, once: bool = False) -> ObservedTransition:
        previous = signal.getsignal(signal.SIGINT)

        def on_interrupt(_signum: int, _frame: object) -> None:
            self.interrupts.interrupt()
            self._wake()

        signal.signal(signal.SIGINT, on_interrupt)
        self._background_dispatch = not once
        try:
            while True:
                self._raise_background_job_error()
                observed = reconcile_once(self)
                if once or not self.interrupts.dispatch_allowed:
                    return observed
                if observed.transition is None:
                    self._wait_for_work()
        finally:
            self._background_dispatch = False
            signal.signal(signal.SIGINT, previous)

    def _raise_background_job_error(self) -> None:
        if self._job_errors:
            raise self._job_errors.pop(0)

    def _wait_for_work(self) -> None:
        self._emit(
            f"heartbeat state=waiting active_jobs={len(self.interrupts.jobs)}"
        )
        self._job_completed.wait(self._poll_seconds)
        self._job_completed.clear()

    def _wake(self) -> None:
        self._job_completed.set()

    def require_worktree_idle(self, worktree: Path, operation: str) -> None:
        """Refuse mutations that could race a currently owned coding job."""
        self.worktree_jobs.require_idle(worktree, operation)


def reconcile_once(coordinator: Coordinator) -> ObservedTransition:
    _apply_pending_controls(coordinator)
    coordinator._recover()
    if coordinator.status().paused or not coordinator.interrupts.dispatch_allowed:
        return ObservedTransition(None, "paused")
    transition = coordinator.services.planner.select()
    if transition is None:
        return ObservedTransition(None, "waiting")
    return _execute_transition(coordinator, transition)


def _apply_pending_controls(coordinator: Coordinator) -> None:
    applied = coordinator.store.apply_pending_controls()
    for request in applied:
        coordinator._emit(
            f"control action={request.kind.value} scope={request.scope.key} result=applied"
        )


def _execute_transition(
    coordinator: Coordinator, transition: object
) -> ObservedTransition:
    worktree_value = getattr(transition, "worktree", None)
    worktree = worktree_value if isinstance(worktree_value, Path) else None
    owner = object()
    if worktree is not None:
        coordinator.worktree_jobs.claim(worktree, owner)
    try:
        job = coordinator.services.supervisor.launch(transition)
    except BaseException:
        if worktree is not None:
            coordinator.worktree_jobs.release(worktree, owner)
        raise
    coordinator.interrupts.track(job)
    coordinator._emit(f"transition action={transition} result=started")
    if coordinator._background_dispatch:
        thread = threading.Thread(
            target=_wait_for_transition,
            args=(coordinator, transition, job, worktree, owner),
            daemon=True,
            name="local-issue-runner-job",
        )
        thread.start()
        return ObservedTransition(transition, "started")
    try:
        coordinator.services.supervisor.wait(job)
    finally:
        coordinator.interrupts.untrack(job)
        if worktree is not None:
            coordinator.worktree_jobs.release(worktree, owner)
    coordinator._emit(f"transition action={transition} result=completed")
    return ObservedTransition(transition, "completed")


def _wait_for_transition(
    coordinator: Coordinator,
    transition: object,
    job: object,
    worktree: Path | None,
    owner: object,
) -> None:
    try:
        coordinator.services.supervisor.wait(job)
    # Preserve worker failures for deterministic propagation on the coordinator thread.
    except Exception as error:  # noqa: BLE001
        coordinator._job_errors.append(error)
        coordinator._emit(f"transition action={transition} result=failed")
    else:
        coordinator._emit(f"transition action={transition} result=completed")
    finally:
        coordinator.interrupts.untrack(job)
        if worktree is not None:
            coordinator.worktree_jobs.release(worktree, owner)
        coordinator._wake()


def decide_next(snapshots: tuple[FeatureSnapshot, ...]) -> NextTransition:
    for snapshot in snapshots:
        if snapshot.ready_issue_numbers:
            return NextTransition(
                TransitionKind.START_CHILD,
                snapshot.parent_issue,
                snapshot.ready_issue_numbers[0],
                f"start child issue {snapshot.ready_issue_numbers[0]}",
            )
    for snapshot in snapshots:
        if snapshot.blockers:
            blocker = snapshot.blockers[0]
            return NextTransition(
                TransitionKind.BLOCKED,
                snapshot.parent_issue,
                detail=f"{blocker.code.value}: {blocker.detail}",
            )
    return NextTransition(TransitionKind.WAIT, detail="no child task is currently ready")


def _print_report(
    report: PlanReport, *, label: str, delivery_mode: str | None = None
) -> None:
    print(f"{label} (read-only)")
    if delivery_mode is not None:
        print(f"Delivery mode: {delivery_mode}")
    for snapshot in report.snapshots:
        children = ", ".join(map(str, snapshot.child_issues)) or "none"
        ready = ", ".join(map(str, snapshot.ready_issue_numbers)) or "none"
        print(
            f"- parent {snapshot.parent_issue}: discovery={snapshot.discovery_mode.value}; "
            f"effective children={children}; ready={ready}; "
            f"integration={next(task.task_id for task in snapshot.tasks if task.issue_number is None)}"
        )
        for blocker in snapshot.blockers:
            print(f"  BLOCKER {blocker.code.value}: {blocker.detail}")
    transition = report.next_transition
    target = (
        f" parent={transition.parent_issue}" if transition.parent_issue is not None else ""
    )
    if transition.issue_number is not None:
        target += f" issue={transition.issue_number}"
    print(
        f"Next transition: {transition.kind.value}{target}"
        + (f" - {transition.detail}" if transition.detail else "")
    )


def plan(
    config_path: str | Path,
    *,
    services: PlanServices,
    label: str = "Plan",
) -> PlanReport:
    config = load_config(config_path)
    snapshots = build_feature_snapshots(
        config,
        services.github,
        completion_satisfied=services.completion_satisfied,
        repair_children=services.repair_children,
    )
    report = PlanReport(snapshots, decide_next(snapshots))
    _print_report(report, label=label, delivery_mode=config.delivery_mode.value)
    if services.repair_children is not None:
        for snapshot in snapshots:
            for repair in services.repair_children(
                snapshot.repository, snapshot.parent_issue
            ):
                print(
                    f"  repair child={repair.issue_number}; "
                    f"origin={repair.finding_identity}; source={repair.source}"
                )
    return report
