from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

import pytest

from local_issue_runner.merge_policy import MergeReadinessDecision, MergeTarget
from local_issue_runner.models import (
    QualificationContext,
    QualificationLevel,
    QualificationReceipt,
)
from local_issue_runner.qualify import QualificationService
from local_issue_runner.reconcile import (
    ParentAutomergeService,
    ParentMergeRequest,
    ParentMergeStatus,
)

HEAD = "a" * 40
MERGED = "b" * 40
FULL_TRANSITIONS = (
    "dependency",
    "parallel_work",
    "feedback",
    "restart_recovery",
    "parent_integration",
)


def context() -> QualificationContext:
    return QualificationContext(
        runtime_identity="runner-runtime-v1",
        tool_versions=(("gh", "2.80.0"), ("git", "2.51.0")),
        policy_fingerprint="merge-policy-v1",
    )


def full_receipt() -> QualificationReceipt:
    return QualificationReceipt(
        level=QualificationLevel.FULL,
        disposable_repository="owner/disposable",
        exercise_id="full-7",
        successful=True,
        observed_transitions=FULL_TRANSITIONS,
        context=context(),
        completed_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
        evidence_paths=("evidence/full.json",),
    )


def request() -> ParentMergeRequest:
    return ParentMergeRequest(
        repository="owner/project",
        parent_issue=100,
        delivery=2,
        pr_number=41,
        head_ref="feature/customer-export",
        expected_head=HEAD,
        base_ref="main",
        selected_children=(101, 102),
    )


@dataclass
class MemoryStore:
    merge_intent: ParentMergeRequest | None = None
    resulting_commit: str | None = None

    def runner_owns_pull_request(self, candidate: ParentMergeRequest) -> bool:
        return True

    def record_parent_merge_intent(self, candidate: ParentMergeRequest) -> None:
        self.merge_intent = candidate

    def record_parent_merge_result(
        self, candidate: ParentMergeRequest, resulting_commit: str
    ) -> None:
        self.resulting_commit = resulting_commit

    def recorded_parent_merge(self, candidate: ParentMergeRequest) -> str | None:
        return self.resulting_commit


@dataclass
class FakeGitHub:
    merged: bool = False
    head: str = HEAD
    merge_calls: list[tuple[int, str, str]] = field(default_factory=list)
    close_calls: list[int] = field(default_factory=list)

    def observe_parent_merge(
        self, repository: str, pr_number: int
    ) -> tuple[bool, str, str | None]:
        return self.merged, self.head, MERGED if self.merged else None

    def merge_parent(
        self,
        repository: str,
        pr_number: int,
        expected_head: str,
        *,
        method: str,
    ) -> str:
        self.merge_calls.append((pr_number, expected_head, method))
        self.merged = True
        return MERGED

    def close_parent(self, repository: str, parent_issue: int) -> None:
        self.close_calls.append(parent_issue)


@dataclass
class FreshFacts:
    integrated: bool = True
    selected_children: tuple[int, ...] = (101, 102)
    complete_children: tuple[int, ...] = (101, 102)
    scope_current: bool = True
    feedback_current: bool = True

    def merged_into_main(
        self, candidate: ParentMergeRequest, resulting_commit: str
    ) -> bool:
        return self.integrated

    def current_selected_children(
        self, candidate: ParentMergeRequest
    ) -> tuple[int, ...]:
        return self.selected_children

    def completed_children(self, candidate: ParentMergeRequest) -> tuple[int, ...]:
        return self.complete_children

    def scope_and_feedback_current(self, candidate: ParentMergeRequest) -> bool:
        return self.scope_current and self.feedback_current


def service(
    *,
    receipt: QualificationReceipt | None,
    facts: FreshFacts | None = None,
) -> tuple[ParentAutomergeService, MemoryStore, FakeGitHub]:
    store = MemoryStore()
    github = FakeGitHub()
    subject = ParentAutomergeService(
        store=store,
        github=github,
        facts=facts or FreshFacts(),
        qualification=QualificationService(receipt=receipt, current=context()),
    )
    return subject, store, github


@pytest.mark.parametrize(
    "receipt",
    [
        None,
        replace(full_receipt(), level=QualificationLevel.CHILD),
        replace(full_receipt(), observed_transitions=FULL_TRANSITIONS[:-1]),
        replace(full_receipt(), successful=False),
    ],
)
def test_merge_parents_is_unavailable_without_full_qualification(
    receipt: QualificationReceipt | None,
) -> None:
    subject, store, github = service(receipt=receipt)

    result = subject.reconcile(
        request(), readiness=MergeReadinessDecision(MergeTarget.PARENT, ())
    )

    assert result.status is ParentMergeStatus.BLOCKED_QUALIFICATION
    assert store.merge_intent is None
    assert github.merge_calls == []


def test_parent_uses_merge_commit_and_closes_only_after_fresh_verification() -> None:
    subject, store, github = service(receipt=full_receipt())

    result = subject.reconcile(
        request(), readiness=MergeReadinessDecision(MergeTarget.PARENT, ())
    )

    assert result.status is ParentMergeStatus.COMPLETED
    assert github.merge_calls == [(41, HEAD, "merge")]
    assert store.resulting_commit == MERGED
    assert github.close_calls == [100]


@pytest.mark.parametrize(
    "facts",
    [
        FreshFacts(integrated=False),
        FreshFacts(selected_children=(101, 102, 103)),
        FreshFacts(complete_children=(101,)),
        FreshFacts(scope_current=False),
        FreshFacts(feedback_current=False),
    ],
)
def test_last_second_changes_preserve_merge_evidence_but_leave_parent_open(
    facts: FreshFacts,
) -> None:
    subject, store, github = service(receipt=full_receipt(), facts=facts)

    result = subject.reconcile(
        request(), readiness=MergeReadinessDecision(MergeTarget.PARENT, ())
    )

    assert result.status is ParentMergeStatus.MERGED_PARENT_OPEN
    assert store.resulting_commit == MERGED
    assert github.close_calls == []
