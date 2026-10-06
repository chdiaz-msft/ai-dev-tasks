from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType


class AutonomyLevel(StrEnum):
    CREATE_PR = "create_pr"
    MERGE_CHILDREN = "merge_children"
    MERGE_PARENTS = "merge_parents"


class DeliveryMode(StrEnum):
    CHILD_PRS = "child_prs"
    LOCAL_COMMITS = "local_commits"


class QualificationLevel(StrEnum):
    CHILD = "child"
    FULL = "full"

    def authorizes(self, required: QualificationLevel) -> bool:
        order = {
            QualificationLevel.CHILD: 1,
            QualificationLevel.FULL: 2,
        }
        return order[self] >= order[required]


@dataclass(frozen=True, slots=True)
class QualificationContext:
    runtime_identity: str
    tool_versions: tuple[tuple[str, str], ...]
    policy_fingerprint: str

    def __post_init__(self) -> None:
        if not self.runtime_identity.strip() or not self.policy_fingerprint.strip():
            raise ValueError("qualification runtime and policy identities are required")
        if not self.tool_versions:
            raise ValueError("qualification requires at least one tool version")
        normalized = tuple(
            sorted((name.strip(), version.strip()) for name, version in self.tool_versions)
        )
        if any(not name or not version for name, version in normalized):
            raise ValueError("qualification tool names and versions are required")
        if len({name for name, _ in normalized}) != len(normalized):
            raise ValueError("qualification tool names must be unique")
        object.__setattr__(self, "tool_versions", normalized)


@dataclass(frozen=True, slots=True)
class QualificationReceipt:
    level: QualificationLevel
    disposable_repository: str
    exercise_id: str
    successful: bool
    observed_transitions: tuple[str, ...]
    context: QualificationContext
    completed_at: datetime
    evidence_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.disposable_repository.strip() or not self.exercise_id.strip():
            raise ValueError("qualification repository and exercise ID are required")
        if self.completed_at.tzinfo is None or self.completed_at.utcoffset() is None:
            raise ValueError("qualification completion time must be timezone-aware")
        transitions = tuple(dict.fromkeys(item.strip() for item in self.observed_transitions))
        evidence = tuple(dict.fromkeys(item.strip() for item in self.evidence_paths))
        if any(not item for item in transitions) or any(not item for item in evidence):
            raise ValueError("qualification transitions and evidence paths cannot be blank")
        object.__setattr__(self, "observed_transitions", transitions)
        object.__setattr__(self, "evidence_paths", evidence)


class InterfaceStatus(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNREADABLE = "unreadable"


class DiscoveryMode(StrEnum):
    NATIVE = "native"
    EXPLICIT = "explicit"


class TaskKind(StrEnum):
    CHILD = "child"
    INTEGRATION = "integration"


class BlockerCode(StrEnum):
    NESTED_CHILD = "nested_child"
    DUPLICATE_CHILD = "duplicate_child"
    INCOMPLETE_DEPENDENCY_READ = "incomplete_dependency_read"
    CROSS_FEATURE_DEPENDENCY = "cross_feature_dependency"
    OUT_OF_SCOPE_DEPENDENCY = "out_of_scope_dependency"
    CROSS_REPOSITORY_DEPENDENCY = "cross_repository_dependency"
    CYCLE = "cycle"


class TransitionKind(StrEnum):
    START_CHILD = "start_child"
    START_INTEGRATION = "start_integration"
    WAIT = "wait"
    BLOCKED = "blocked"


class ControlKind(StrEnum):
    PAUSE = "pause"
    RESUME = "resume"


class ActionState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    AMBIGUOUS = "ambiguous"

    @property
    def terminal(self) -> bool:
        return self in {
            ActionState.SUCCEEDED,
            ActionState.FAILED,
            ActionState.AMBIGUOUS,
        }


class CandidateKind(StrEnum):
    RECOVERY = "recovery"
    ACTIONABLE_PR = "actionable_pr"
    PUBLISHED_PR = "published_pr"
    START_CHILD = "start_child"


class DeliveryStage(StrEnum):
    IMPLEMENTING = "implementing"
    WAITING_PR = "waiting_pr"
    ACTIONABLE_PR = "actionable_pr"
    RECOVERING = "recovering"
    REOPENED_PREREQUISITE = "reopened_prerequisite"


class FeedbackKind(StrEnum):
    REVIEW_THREAD_COMMENT = "review_thread_comment"
    ISSUE_COMMENT = "issue_comment"
    REVIEW_BODY = "review_body"


class ThreadState(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class FeedbackDisposition(StrEnum):
    IMPLEMENTED = "implemented"
    NO_CHANGE = "no_change"
    INFORMATIONAL = "informational"
    NEEDS_HUMAN = "needs_human"


class ProblemCategory(StrEnum):
    CODE = "code"
    LOCAL_VALIDATION = "local_validation"
    INFRASTRUCTURE = "infrastructure"
    CREDENTIALS = "credentials"
    UNRELATED = "unrelated"
    UNCLEAR_REQUIREMENT = "unclear_requirement"


@dataclass(frozen=True, slots=True)
class ProblemRecord:
    identity: str
    category: ProblemCategory
    detail: str
    streak: int
    stopped: bool
    retry_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.identity.strip() or not self.detail.strip():
            raise ValueError("problem identity and detail are required")
        if self.streak < 0:
            raise ValueError("problem streak cannot be negative")


@dataclass(frozen=True, slots=True)
class OperationalBlocker:
    identity: str
    category: ProblemCategory
    detail: str

    def __post_init__(self) -> None:
        if not self.identity.strip() or not self.detail.strip():
            raise ValueError("blocker identity and detail are required")


@dataclass(frozen=True, slots=True)
class FeedbackItem:
    kind: FeedbackKind
    item_id: str
    author_login: str
    body: str
    created_at: str
    updated_at: str
    thread_id: str | None = None
    thread_state: ThreadState | None = None

    def __post_init__(self) -> None:
        if not all(
            (
                self.item_id.strip(),
                self.author_login.strip(),
                self.created_at.strip(),
                self.updated_at.strip(),
            )
        ):
            raise ValueError("feedback identity, author, and timestamps are required")
        if self.kind is FeedbackKind.REVIEW_THREAD_COMMENT and not self.thread_id:
            raise ValueError("review-thread feedback requires a thread ID")
        if self.kind is not FeedbackKind.REVIEW_THREAD_COMMENT and (
            self.thread_id is not None or self.thread_state is not None
        ):
            raise ValueError("only review-thread feedback may have thread state")

    @property
    def identity(self) -> str:
        return f"{self.kind.value}:{self.item_id}"


@dataclass(frozen=True, slots=True)
class FeedbackDecision:
    disposition: FeedbackDisposition
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("feedback decisions require a reason")


@dataclass(frozen=True, slots=True)
class RepairEvidence:
    head_sha: str
    validation_summary: str

    def __post_init__(self) -> None:
        if not self.head_sha.strip() or not self.validation_summary.strip():
            raise ValueError("repair evidence requires a head and validation summary")


@dataclass(frozen=True, slots=True)
class FeedbackReconcileResult:
    handled_feedback: tuple[str, ...]
    quiet_period_restarted: bool


@dataclass(frozen=True, slots=True)
class SchedulingCandidate:
    parent_issue: int
    issue_number: int
    delivery: int
    worktree: Path
    kind: CandidateKind = CandidateKind.START_CHILD
    downstream_unlock_count: int = 0

    def __post_init__(self) -> None:
        if min(self.parent_issue, self.issue_number, self.delivery) <= 0:
            raise ValueError("scheduling candidate numbers must be positive")
        if self.downstream_unlock_count < 0:
            raise ValueError("downstream unlock count cannot be negative")


@dataclass(frozen=True, slots=True)
class ActiveDelivery:
    parent_issue: int
    issue_number: int
    delivery: int
    worktree: Path
    stage: DeliveryStage
    job_running: bool = False

    def __post_init__(self) -> None:
        if min(self.parent_issue, self.issue_number, self.delivery) <= 0:
            raise ValueError("active delivery numbers must be positive")

    @property
    def identity(self) -> tuple[int, int]:
        return self.issue_number, self.delivery


@dataclass(frozen=True, slots=True)
class FeatureWork:
    parent_issue: int
    ready: tuple[SchedulingCandidate, ...] = ()
    active: tuple[ActiveDelivery, ...] = ()
    max_active_slots: int | None = None

    def __post_init__(self) -> None:
        if self.parent_issue <= 0:
            raise ValueError("parent issue must be positive")
        if self.max_active_slots is not None and self.max_active_slots <= 0:
            raise ValueError("feature slot limit must be positive")
        if any(item.parent_issue != self.parent_issue for item in (*self.ready, *self.active)):
            raise ValueError("feature work cannot contain another parent's delivery")
        identities = [item.identity for item in self.active]
        if len(identities) != len(set(identities)):
            raise ValueError("an active delivery can consume only one slot")

    @property
    def active_slot_count(self) -> int:
        return len(self.active)


@dataclass(frozen=True, slots=True)
class SchedulingDecision:
    candidate: SchedulingCandidate | None
    pause_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ControlScope:
    parent_issue: int | None = None
    issue_number: int | None = None

    def __post_init__(self) -> None:
        if self.parent_issue is not None and self.parent_issue <= 0:
            raise ValueError("parent issue must be positive")
        if self.issue_number is not None and self.issue_number <= 0:
            raise ValueError("issue number must be positive")
        if self.issue_number is not None and self.parent_issue is None:
            raise ValueError("an issue control scope requires its parent issue")

    @property
    def key(self) -> str:
        parent = "*" if self.parent_issue is None else str(self.parent_issue)
        issue = "*" if self.issue_number is None else str(self.issue_number)
        return f"{parent}:{issue}"


@dataclass(frozen=True, slots=True)
class ControlRequest:
    request_id: int
    kind: ControlKind
    scope: ControlScope
    created_at: str


@dataclass(frozen=True, slots=True)
class PauseState:
    scope: ControlScope
    paused: bool
    updated_at: str | None = None


@dataclass(frozen=True, slots=True)
class ActionIntent:
    key: str
    kind: str
    target: str
    expected_input: str

    def __post_init__(self) -> None:
        if not all((self.key, self.kind, self.target, self.expected_input)):
            raise ValueError("action intent fields must not be empty")


@dataclass(frozen=True, slots=True)
class ActionOutcome:
    intent_key: str
    state: ActionState
    detail: str | None = None
    observed_at: str | None = None

    def __post_init__(self) -> None:
        if not self.state.terminal:
            raise ValueError("action outcomes must use a terminal state")


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    pid: int
    created_at: str
    session_id: str

    def __post_init__(self) -> None:
        if self.pid <= 0:
            raise ValueError("process ID must be positive")
        if not self.created_at or not self.session_id:
            raise ValueError("process creation time and session ID are required")


@dataclass(frozen=True, slots=True)
class ProcessRecord:
    worktree: Path
    identity: ProcessIdentity
    state: ActionState


@dataclass(frozen=True, slots=True)
class ToolProbe:
    name: str
    status: InterfaceStatus
    version: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ParentConfig:
    issue: int
    feature_branch: str
    child_issues: tuple[int, ...] | None = None

    @property
    def uses_native_discovery(self) -> bool:
        return self.child_issues is None


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    version: int
    repository: str
    repository_path: Path
    remote: str
    main_branch: str
    delivery_mode: DeliveryMode
    poll_seconds: int
    agent_timeout_seconds: int
    max_active_issues_per_feature: int
    ci_wait_timeout_seconds: int
    merge_quiet_seconds: int
    autonomy: AutonomyLevel
    validation_commands: tuple[tuple[str, ...], ...]
    required_checks: tuple[str, ...]
    parents: tuple[ParentConfig, ...]
    config_path: Path


@dataclass(frozen=True, slots=True)
class IssueFact:
    repository: str
    number: int
    title: str
    state: str
    parent_issue: int | None = None
    source_revision: str | None = None
    body: str = ""


@dataclass(frozen=True, slots=True)
class DependencyFact:
    repository: str
    prerequisite: int
    dependent: int
    relationship_id: str | None = None
    source_revision: str | None = None


@dataclass(frozen=True, slots=True)
class DependencyRead:
    complete: bool
    edges: tuple[DependencyFact, ...]
    detail: str | None = None


TaskId = int | str


@dataclass(frozen=True, slots=True)
class SnapshotTask:
    task_id: TaskId
    kind: TaskKind
    issue_number: int | None = None
    title: str | None = None
    source_revision: str | None = None

    def __post_init__(self) -> None:
        if self.kind is TaskKind.CHILD and self.issue_number is None:
            raise ValueError("child snapshot tasks require an issue number")
        if self.kind is TaskKind.INTEGRATION and self.issue_number is not None:
            raise ValueError("integration snapshot tasks cannot have an issue number")


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    prerequisite_task_id: TaskId
    dependent_task_id: TaskId
    source_relationship_id: str | None = None
    source_revision: str | None = None

    def __post_init__(self) -> None:
        if self.prerequisite_task_id == self.dependent_task_id:
            raise ValueError("dependency edges cannot be self-referential")


def build_dependency_maps(
    task_ids: Iterable[TaskId], edges: Iterable[DependencyEdge]
) -> tuple[dict[TaskId, list[TaskId]], dict[TaskId, list[TaskId]]]:
    predecessors = {task_id: [] for task_id in task_ids}
    successors = {task_id: [] for task_id in predecessors}
    for edge in edges:
        if (
            edge.prerequisite_task_id not in predecessors
            or edge.dependent_task_id not in predecessors
        ):
            raise ValueError("dependency edge endpoints must belong to the snapshot")
        predecessors[edge.dependent_task_id].append(edge.prerequisite_task_id)
        successors[edge.prerequisite_task_id].append(edge.dependent_task_id)
    return predecessors, successors


@dataclass(frozen=True, slots=True)
class SnapshotBlocker:
    code: BlockerCode
    detail: str
    issue_numbers: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class FeatureSnapshot:
    repository: str
    parent_issue: int
    feature_branch: str
    discovery_mode: DiscoveryMode
    child_issues: tuple[int, ...]
    tasks: tuple[SnapshotTask, ...]
    dependency_edges: tuple[DependencyEdge, ...] = ()
    complete: bool = True
    source_revision: str | None = None
    blockers: tuple[SnapshotBlocker, ...] = ()
    ready_issue_numbers: tuple[int, ...] = ()
    blocked_issue_numbers: tuple[int, ...] = ()
    predecessors: Mapping[TaskId, tuple[TaskId, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("snapshot task IDs must be unique")
        task_id_set = set(task_ids)
        predecessors, _ = build_dependency_maps(task_ids, self.dependency_edges)
        if self.predecessors:
            supplied = {
                task_id: tuple(values)
                for task_id, values in self.predecessors.items()
            }
            if set(supplied) != task_id_set:
                raise ValueError("predecessor map must contain every snapshot task")
            derived = {
                task_id: tuple(values)
                for task_id, values in predecessors.items()
            }
            if self.dependency_edges and supplied != derived:
                raise ValueError("predecessor map does not match dependency edges")
            normalized = supplied
        else:
            normalized = {
                task_id: tuple(values)
                for task_id, values in predecessors.items()
            }
        object.__setattr__(self, "predecessors", MappingProxyType(normalized))

    @classmethod
    def fixture(
        cls,
        *,
        parent_issue: int,
        child_issues: tuple[int, ...],
        repository: str = "owner/project",
        feature_branch: str | None = None,
    ) -> FeatureSnapshot:
        integration_id = f"integration:{parent_issue}"
        tasks = tuple(
            SnapshotTask(number, TaskKind.CHILD, issue_number=number)
            for number in child_issues
        ) + (SnapshotTask(integration_id, TaskKind.INTEGRATION),)
        edges = tuple(
            DependencyEdge(number, integration_id) for number in child_issues
        )
        return cls(
            repository=repository,
            parent_issue=parent_issue,
            feature_branch=feature_branch or f"feature/{parent_issue}",
            discovery_mode=DiscoveryMode.EXPLICIT,
            child_issues=child_issues,
            tasks=tasks,
            dependency_edges=edges,
            ready_issue_numbers=child_issues,
        )


@dataclass(frozen=True, slots=True)
class NextTransition:
    kind: TransitionKind
    parent_issue: int | None = None
    issue_number: int | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class PlanReport:
    snapshots: tuple[FeatureSnapshot, ...]
    next_transition: NextTransition


@dataclass(frozen=True, slots=True)
class ChildPRIdentity:
    repository: str
    parent_issue: int
    issue_number: int
    delivery: int
    work_id: str

    def __post_init__(self) -> None:
        if not self.repository or not self.work_id:
            raise ValueError("repository and work ID are required")
        if min(self.parent_issue, self.issue_number, self.delivery) <= 0:
            raise ValueError("child PR identity numbers must be positive")

    @property
    def key(self) -> str:
        return (
            f"{self.repository}:{self.parent_issue}:{self.issue_number}:"
            f"{self.delivery}:{self.work_id}"
        )


@dataclass(frozen=True, slots=True)
class ChildPRRequest:
    identity: ChildPRIdentity
    head_ref: str
    head_sha: str
    base_ref: str
    title: str
    body: str

    def __post_init__(self) -> None:
        if not all((self.head_ref, self.head_sha, self.base_ref, self.title)):
            raise ValueError("child PR request fields must not be empty")


class ChildPRState(StrEnum):
    READY_TO_PUSH = "ready_to_push"
    READY_TO_CREATE = "ready_to_create"
    OPEN = "open"
    BLOCKED_EXTERNAL = "blocked_external"
    BLOCKED_AMBIGUOUS = "blocked_ambiguous"
    ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class ChildPRResult:
    state: ChildPRState
    pr_number: int | None = None
    detail: str | None = None

    @property
    def blocked(self) -> bool:
        return self.state in {
            ChildPRState.BLOCKED_EXTERNAL,
            ChildPRState.BLOCKED_AMBIGUOUS,
            ChildPRState.ABANDONED,
        }


@dataclass(frozen=True, slots=True)
class IssueDeliveryRecord:
    repository: str
    parent_issue: int
    issue_number: int
    delivery: int
    scope_fingerprint: str
    branch: str
    worktree: Path

    def __post_init__(self) -> None:
        if min(self.parent_issue, self.issue_number, self.delivery) <= 0:
            raise ValueError("delivery record numbers must be positive")
        if not all((self.repository, self.scope_fingerprint, self.branch)):
            raise ValueError("delivery record identity and scope are required")

    @property
    def key(self) -> str:
        return (
            f"{self.repository}:{self.parent_issue}:"
            f"{self.issue_number}:{self.delivery}"
        )


@dataclass(frozen=True, slots=True)
class ParentDeliveryRecord:
    repository: str
    parent_issue: int
    delivery: int
    selected_children: tuple[int, ...]

    def __post_init__(self) -> None:
        if min(self.parent_issue, self.delivery) <= 0:
            raise ValueError("parent delivery numbers must be positive")
        if not self.repository or any(number <= 0 for number in self.selected_children):
            raise ValueError("parent delivery identity and children are required")

    @property
    def key(self) -> str:
        return f"{self.repository}:{self.parent_issue}:{self.delivery}"


@dataclass(frozen=True, slots=True)
class IntegrationRepairChildRecord:
    repository: str
    parent_issue: int
    finding_identity: str
    issue_number: int
    source: str

    def __post_init__(self) -> None:
        if min(self.parent_issue, self.issue_number) <= 0:
            raise ValueError("repair child numbers must be positive")
        if not all(
            value.strip()
            for value in (self.repository, self.finding_identity, self.source)
        ):
            raise ValueError("repair child origin is required")


@dataclass(frozen=True, slots=True)
class DependencyInvalidation:
    repository: str
    parent_issue: int
    prerequisite_issue: int
    dependent_issue: int
    prerequisite_delivery: int

    def __post_init__(self) -> None:
        if min(
            self.parent_issue,
            self.prerequisite_issue,
            self.dependent_issue,
            self.prerequisite_delivery,
        ) <= 0:
            raise ValueError("dependency invalidation numbers must be positive")
        if self.prerequisite_issue == self.dependent_issue:
            raise ValueError("a delivery cannot invalidate itself")
