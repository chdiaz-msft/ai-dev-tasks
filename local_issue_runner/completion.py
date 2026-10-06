from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class CompletionBlocked(RuntimeError):
    """A delivery cannot safely be treated as complete."""


class CompletionStatus(StrEnum):
    WAITING = "waiting"
    COMPLETE = "complete"
    ACKNOWLEDGED = "acknowledged"


class CompletionMode(StrEnum):
    PULL_REQUEST = "pull_request"
    LOCAL_COMMIT = "local_commit"
    ACKNOWLEDGEMENT = "acknowledgement"


@dataclass(frozen=True, slots=True)
class CompletionDelivery:
    repository: str
    parent_issue: int
    issue_number: int
    delivery: int
    pr_number: int | None
    base_ref: str
    base_commit: str
    work_head: str | None
    scope_fingerprint: str
    job_succeeded: bool

    def __post_init__(self) -> None:
        if not all(
            (
                self.repository,
                self.base_ref,
                self.base_commit,
                self.scope_fingerprint,
            )
        ):
            raise ValueError("completion delivery identity and scope are required")
        if min(self.parent_issue, self.issue_number, self.delivery) <= 0:
            raise ValueError("completion delivery numbers must be positive")
        if self.pr_number is not None and self.pr_number <= 0:
            raise ValueError("pull request number must be positive")
        if (self.pr_number is None) != (self.work_head is None):
            raise ValueError(
                "pull request number and work head must be recorded together"
            )

    @property
    def key(self) -> str:
        return (
            f"{self.repository}:{self.parent_issue}:{self.issue_number}:{self.delivery}"
        )

    @property
    def requires_pull_request_merge(self) -> bool:
        return self.pr_number is not None


@dataclass(frozen=True, slots=True)
class PullRequestObservation:
    number: int
    state: str
    merged: bool
    head_sha: str
    base_ref: str
    resulting_commit: str | None


@dataclass(frozen=True, slots=True)
class CompletionEvidence:
    delivery: CompletionDelivery
    pr_number: int
    resulting_commit: str
    recorded_base: str
    base_ref: str
    work_head: str
    scope_fingerprint: str


@dataclass(frozen=True, slots=True)
class LocalCommitCompletionEvidence:
    delivery: CompletionDelivery
    feature_branch: str
    feature_base: str
    resulting_commit: str
    scope_fingerprint: str
    validation_identity: str

    def __post_init__(self) -> None:
        if not all(
            (
                self.feature_branch,
                self.feature_base,
                self.resulting_commit,
                self.scope_fingerprint,
                self.validation_identity,
            )
        ):
            raise ValueError("local commit completion evidence must be complete")
        if self.delivery.requires_pull_request_merge:
            raise ValueError("local commit evidence cannot reference a pull request")
        if (
            self.feature_branch != self.delivery.base_ref
            or self.feature_base != self.delivery.base_commit
            or self.scope_fingerprint != self.delivery.scope_fingerprint
        ):
            raise ValueError("local commit evidence does not match its delivery")


AnyCompletionEvidence = CompletionEvidence | LocalCommitCompletionEvidence


@dataclass(frozen=True, slots=True)
class CompletionResult:
    status: CompletionStatus
    evidence: AnyCompletionEvidence | None = None


@dataclass(frozen=True, slots=True)
class ParentCompletionCheck:
    integrated: bool
    selected_children_unchanged: bool
    all_selected_children_complete: bool
    scope_and_feedback_current: bool

    @property
    def complete(self) -> bool:
        return (
            self.integrated
            and self.selected_children_unchanged
            and self.all_selected_children_complete
            and self.scope_and_feedback_current
        )


class CompletionStore(Protocol):
    def completion(
        self,
        delivery_key: str,
        mode: CompletionMode = CompletionMode.PULL_REQUEST,
    ) -> AnyCompletionEvidence | None: ...

    def record_completion(self, evidence: AnyCompletionEvidence) -> None: ...

    def acknowledgement(self, delivery_key: str) -> str | None: ...

    def record_acknowledgement(self, delivery_key: str, reason: str) -> None: ...


class CompletionGit(Protocol):
    def commit_available_in_branch(
        self, repository: str, commit: str, branch: str
    ) -> bool: ...


class CompletionGitHub(Protocol):
    def get_pull_request(
        self, repository: str, number: int
    ) -> PullRequestObservation | None: ...

    def get_issue_state(self, repository: str, number: int) -> str: ...

    def close_issue_as_completed(
        self, repository: str, number: int, evidence_url: str
    ) -> None: ...


class ChildCompletionService:
    def __init__(
        self,
        *,
        store: CompletionStore,
        git: CompletionGit,
        github: CompletionGitHub,
    ) -> None:
        self.store = store
        self.git = git
        self.github = github

    @staticmethod
    def _evidence_url(evidence: AnyCompletionEvidence) -> str:
        if isinstance(evidence, LocalCommitCompletionEvidence):
            return (
                f"https://github.com/{evidence.delivery.repository}/commit/"
                f"{evidence.resulting_commit}"
            )
        return (
            f"https://github.com/{evidence.delivery.repository}/pull/"
            f"{evidence.pr_number}"
        )

    def _observe_merge(self, delivery: CompletionDelivery) -> CompletionEvidence | None:
        if delivery.pr_number is None or delivery.work_head is None:
            return None
        pull_request = self.github.get_pull_request(
            delivery.repository, delivery.pr_number
        )
        if pull_request is None or not pull_request.merged:
            return None
        if (
            pull_request.number != delivery.pr_number
            or pull_request.head_sha != delivery.work_head
            or pull_request.base_ref != delivery.base_ref
        ):
            raise CompletionBlocked(
                "merged pull request does not match the recorded delivery"
            )
        resulting_commit = pull_request.resulting_commit
        if not resulting_commit:
            raise CompletionBlocked("merged pull request has no resulting commit")
        if not self.git.commit_available_in_branch(
            delivery.repository, resulting_commit, delivery.base_ref
        ):
            raise CompletionBlocked(
                "merged pull request commit is not available in its recorded base branch"
            )
        return CompletionEvidence(
            delivery=delivery,
            pr_number=pull_request.number,
            resulting_commit=resulting_commit,
            recorded_base=delivery.base_commit,
            base_ref=delivery.base_ref,
            work_head=delivery.work_head,
            scope_fingerprint=delivery.scope_fingerprint,
        )

    @staticmethod
    def _evidence_matches_delivery(
        evidence: AnyCompletionEvidence,
        delivery: CompletionDelivery,
    ) -> bool:
        if evidence.delivery.key != delivery.key:
            return False
        if isinstance(evidence, LocalCommitCompletionEvidence):
            return (
                not delivery.requires_pull_request_merge
                and evidence.feature_branch == delivery.base_ref
                and evidence.feature_base == delivery.base_commit
                and evidence.scope_fingerprint == delivery.scope_fingerprint
            )
        return (
            delivery.pr_number == evidence.pr_number
            and delivery.work_head == evidence.work_head
            and delivery.base_ref == evidence.base_ref
            and delivery.base_commit == evidence.recorded_base
            and delivery.scope_fingerprint == evidence.scope_fingerprint
        )

    def _reconcile_pull_request(self, delivery: CompletionDelivery) -> CompletionResult:
        evidence = self.store.completion(delivery.key)
        if (
            evidence is None
            and self.store.completion(delivery.key, CompletionMode.LOCAL_COMMIT)
            is not None
        ):
            raise CompletionBlocked(
                "delivery has completion evidence for a different mode"
            )
        if evidence is not None and (
            not isinstance(evidence, CompletionEvidence)
            or not self._evidence_matches_delivery(evidence, delivery)
        ):
            raise CompletionBlocked(
                "completion evidence does not match the recorded delivery"
            )
        if evidence is None:
            evidence = self._observe_merge(delivery)
            if evidence is None:
                return CompletionResult(CompletionStatus.WAITING)
            self.store.record_completion(evidence)

        self.github.close_issue_as_completed(
            delivery.repository,
            delivery.issue_number,
            self._evidence_url(evidence),
        )
        return CompletionResult(CompletionStatus.COMPLETE, evidence)

    def _reconcile_local_commit(
        self,
        delivery: CompletionDelivery,
        *,
        resulting_commit: str | None,
        validation_identity: str | None,
    ) -> CompletionResult:
        evidence = self.store.completion(delivery.key, CompletionMode.LOCAL_COMMIT)
        if evidence is None and self.store.completion(delivery.key) is not None:
            raise CompletionBlocked(
                "delivery has completion evidence for a different mode"
            )
        if evidence is not None and (
            not isinstance(evidence, LocalCommitCompletionEvidence)
            or not self._evidence_matches_delivery(evidence, delivery)
        ):
            raise CompletionBlocked(
                "completion evidence does not match the recorded delivery"
            )
        if evidence is None:
            if delivery.requires_pull_request_merge:
                raise CompletionBlocked(
                    "local commit completion cannot replace a pull request requirement"
                )
            if not delivery.job_succeeded:
                raise CompletionBlocked(
                    "local commit completion requires a successful delivery"
                )
            commit = (resulting_commit or "").strip()
            validation = (validation_identity or "").strip()
            if not commit or not validation:
                raise CompletionBlocked(
                    "local commit and validation identity are required"
                )
            if not self.git.commit_available_in_branch(
                delivery.repository, commit, delivery.base_ref
            ):
                raise CompletionBlocked(
                    "local completion commit is not available in its feature branch"
                )
            evidence = LocalCommitCompletionEvidence(
                delivery=delivery,
                feature_branch=delivery.base_ref,
                feature_base=delivery.base_commit,
                resulting_commit=commit,
                scope_fingerprint=delivery.scope_fingerprint,
                validation_identity=validation,
            )
            self.store.record_completion(evidence)

        self.github.close_issue_as_completed(
            delivery.repository,
            delivery.issue_number,
            self._evidence_url(evidence),
        )
        return CompletionResult(CompletionStatus.COMPLETE, evidence)

    def reconcile(
        self,
        delivery: CompletionDelivery,
        *,
        mode: CompletionMode = CompletionMode.PULL_REQUEST,
        resulting_commit: str | None = None,
        validation_identity: str | None = None,
    ) -> CompletionResult:
        if mode is CompletionMode.PULL_REQUEST:
            return self._reconcile_pull_request(delivery)
        if mode is CompletionMode.LOCAL_COMMIT:
            return self._reconcile_local_commit(
                delivery,
                resulting_commit=resulting_commit,
                validation_identity=validation_identity,
            )
        raise CompletionBlocked("acknowledgement completion must use acknowledge()")

    def acknowledge(
        self, delivery: CompletionDelivery, *, reason: str
    ) -> CompletionResult:
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("an acknowledgement reason is required")
        if delivery.requires_pull_request_merge:
            raise CompletionBlocked(
                "acknowledgement cannot override a pull request merge requirement"
            )
        if not delivery.job_succeeded:
            raise CompletionBlocked(
                "no-code work must have a successful delivery before acknowledgement"
            )
        if (
            self.github.get_issue_state(
                delivery.repository, delivery.issue_number
            ).upper()
            != "CLOSED"
        ):
            raise CompletionBlocked(
                "no-code work must already be closed before acknowledgement"
            )
        self.store.record_acknowledgement(delivery.key, normalized_reason)
        return CompletionResult(CompletionStatus.ACKNOWLEDGED)

    def _merged_pr_dependency_satisfied(
        self,
        delivery: CompletionDelivery,
        evidence: CompletionEvidence,
        prospective_base_ref: str,
    ) -> bool:
        if (
            self.github.get_issue_state(
                delivery.repository, delivery.issue_number
            ).upper()
            != "CLOSED"
        ):
            return False
        return self.git.commit_available_in_branch(
            delivery.repository,
            evidence.resulting_commit,
            prospective_base_ref,
        )

    def _no_code_dependency_satisfied(self, delivery: CompletionDelivery) -> bool:
        return (
            self.store.acknowledgement(delivery.key) is not None
            and self.github.get_issue_state(
                delivery.repository, delivery.issue_number
            ).upper()
            == "CLOSED"
        )

    def dependency_satisfied(
        self,
        delivery: CompletionDelivery,
        *,
        prospective_base_ref: str,
        mode: CompletionMode | None = None,
    ) -> bool:
        effective_mode = mode
        if effective_mode is None:
            effective_mode = (
                CompletionMode.PULL_REQUEST
                if delivery.requires_pull_request_merge
                else CompletionMode.ACKNOWLEDGEMENT
            )
        if effective_mode is CompletionMode.PULL_REQUEST:
            evidence = self.store.completion(delivery.key)
            return (
                isinstance(evidence, CompletionEvidence)
                and self._evidence_matches_delivery(evidence, delivery)
                and self._merged_pr_dependency_satisfied(
                    delivery,
                    evidence,
                    prospective_base_ref,
                )
            )
        if effective_mode is CompletionMode.LOCAL_COMMIT:
            evidence = self.store.completion(delivery.key, CompletionMode.LOCAL_COMMIT)
            return (
                isinstance(evidence, LocalCommitCompletionEvidence)
                and self._evidence_matches_delivery(evidence, delivery)
                and self.github.get_issue_state(
                    delivery.repository, delivery.issue_number
                ).upper()
                == "CLOSED"
                and self.git.commit_available_in_branch(
                    delivery.repository,
                    evidence.resulting_commit,
                    prospective_base_ref,
                )
            )
        return self._no_code_dependency_satisfied(delivery)
