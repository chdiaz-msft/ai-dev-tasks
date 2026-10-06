from __future__ import annotations

from datetime import datetime
from typing import Protocol

from local_issue_runner.models import (
    AutonomyLevel,
    QualificationContext,
    QualificationLevel,
    QualificationReceipt,
)

__all__ = [
    "CHILD_REQUIRED_TRANSITIONS",
    "FULL_REQUIRED_TRANSITIONS",
    "QualificationContext",
    "QualificationExercise",
    "QualificationLevel",
    "QualificationReceipt",
    "QualificationService",
]

CHILD_REQUIRED_TRANSITIONS = frozenset({"child_merge", "restart_recovery"})
FULL_REQUIRED_TRANSITIONS = frozenset(
    {
        "dependency",
        "parallel_work",
        "feedback",
        "restart_recovery",
        "parent_integration",
    }
)


class ReceiptStore(Protocol):
    def record(self, receipt: QualificationReceipt) -> None: ...


class QualificationService:
    """Evaluates a receipt against the currently installed safety context."""

    def __init__(
        self,
        *,
        receipt: QualificationReceipt | None,
        current: QualificationContext,
    ) -> None:
        self.receipt = receipt
        self.current = current

    def valid_for(self, level: QualificationLevel) -> bool:
        receipt = self.receipt
        if (
            receipt is None
            or not receipt.successful
            or not receipt.level.authorizes(level)
            or receipt.context != self.current
        ):
            return False
        required = (
            FULL_REQUIRED_TRANSITIONS
            if receipt.level is QualificationLevel.FULL
            else CHILD_REQUIRED_TRANSITIONS
        )
        return required.issubset(receipt.observed_transitions)

    def effective_autonomy(self, requested: AutonomyLevel) -> AutonomyLevel:
        """Cap configured autonomy at the highest currently qualified level."""
        if requested is AutonomyLevel.CREATE_PR:
            return AutonomyLevel.CREATE_PR
        if (
            requested is AutonomyLevel.MERGE_PARENTS
            and self.parent_merge_valid
        ):
            return AutonomyLevel.MERGE_PARENTS
        if self.child_merge_valid:
            return AutonomyLevel.MERGE_CHILDREN
        return AutonomyLevel.CREATE_PR

    @property
    def child_merge_valid(self) -> bool:
        return self.valid_for(QualificationLevel.CHILD)

    @property
    def parent_merge_valid(self) -> bool:
        return self.valid_for(QualificationLevel.FULL)


class QualificationExercise:
    """Records the observed result of an explicitly authorized live exercise."""

    def __init__(self, store: ReceiptStore) -> None:
        self._store = store

    def complete_child(
        self,
        *,
        disposable_repository: str,
        exercise_id: str,
        context: QualificationContext,
        observed_transitions: tuple[str, ...],
        completed_at: datetime,
        evidence_paths: tuple[str, ...],
    ) -> QualificationReceipt:
        successful = (
            CHILD_REQUIRED_TRANSITIONS.issubset(observed_transitions)
            and bool(evidence_paths)
        )
        receipt = QualificationReceipt(
            level=QualificationLevel.CHILD,
            disposable_repository=disposable_repository,
            exercise_id=exercise_id,
            successful=successful,
            observed_transitions=observed_transitions,
            context=context,
            completed_at=completed_at,
            evidence_paths=evidence_paths,
        )
        self._store.record(receipt)
        return receipt

    def complete_full(
        self,
        *,
        disposable_repository: str,
        exercise_id: str,
        context: QualificationContext,
        observed_transitions: tuple[str, ...],
        completed_at: datetime,
        evidence_paths: tuple[str, ...],
    ) -> QualificationReceipt:
        successful = (
            FULL_REQUIRED_TRANSITIONS.issubset(observed_transitions)
            and bool(evidence_paths)
        )
        receipt = QualificationReceipt(
            level=QualificationLevel.FULL,
            disposable_repository=disposable_repository,
            exercise_id=exercise_id,
            successful=successful,
            observed_transitions=observed_transitions,
            context=context,
            completed_at=completed_at,
            evidence_paths=evidence_paths,
        )
        self._store.record(receipt)
        return receipt
