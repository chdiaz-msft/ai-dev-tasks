from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from local_issue_runner.locking import WindowsJobObject
from local_issue_runner.models import (
    ActiveDelivery,
    CandidateKind,
    DeliveryMode,
    DeliveryStage,
    FeatureSnapshot,
    FeatureWork,
    NextTransition,
    SchedulingCandidate,
    SchedulingDecision,
)
from local_issue_runner.reconcile import decide_next

__all__ = [
    "ActiveDelivery",
    "CandidateKind",
    "DeliveryMode",
    "DeliveryStage",
    "FeatureWork",
    "SchedulingCandidate",
    "SchedulingPolicy",
    "SnapshotScheduler",
    "SubprocessSupervisor",
]

_KIND_PRIORITY = {
    CandidateKind.RECOVERY: 0,
    CandidateKind.ACTIONABLE_PR: 1,
    CandidateKind.PUBLISHED_PR: 2,
    CandidateKind.START_CHILD: 3,
}


@dataclass(frozen=True, slots=True)
class SchedulingPolicy:
    default_feature_slots: int = 5
    delivery_mode: DeliveryMode = DeliveryMode.CHILD_PRS

    def __post_init__(self) -> None:
        if self.default_feature_slots <= 0:
            raise ValueError("default feature slot limit must be positive")

    @classmethod
    def for_delivery_mode(
        cls,
        delivery_mode: DeliveryMode,
        *,
        default_feature_slots: int = 5,
    ) -> SchedulingPolicy:
        return cls(
            default_feature_slots=default_feature_slots,
            delivery_mode=delivery_mode,
        )

    def select(
        self,
        features: Sequence[FeatureWork],
        *,
        last_served_parent: int | None = None,
    ) -> SchedulingDecision:
        local_owner = self._local_active_owner(features)
        eligible_by_parent = {
            feature.parent_issue: self._eligible(feature, local_owner)
            for feature in features
        }
        candidate = self._select_candidate(eligible_by_parent, last_served_parent)
        if candidate is not None:
            return SchedulingDecision(candidate)
        return SchedulingDecision(None, self._pause_reasons(features))

    def _select_candidate(
        self,
        eligible_by_parent: dict[int, tuple[SchedulingCandidate, ...]],
        last_served_parent: int | None,
    ) -> SchedulingCandidate | None:
        priority = min(
            (
                _KIND_PRIORITY[candidate.kind]
                for candidates in eligible_by_parent.values()
                for candidate in candidates
            ),
            default=None,
        )
        if priority is None:
            return None

        parents = sorted(
            parent
            for parent, candidates in eligible_by_parent.items()
            if any(_KIND_PRIORITY[candidate.kind] == priority for candidate in candidates)
        )
        parent = self._next_parent(parents, last_served_parent)
        return min(
            (
                candidate
                for candidate in eligible_by_parent[parent]
                if _KIND_PRIORITY[candidate.kind] == priority
            ),
            key=lambda candidate: (
                -candidate.downstream_unlock_count,
                candidate.issue_number,
            ),
        )

    def _eligible(
        self,
        feature: FeatureWork,
        local_owner: tuple[int, int, int] | None,
    ) -> tuple[SchedulingCandidate, ...]:
        active = {
            delivery.identity: delivery
            for delivery in feature.active
        }
        limit = feature.max_active_slots or self.default_feature_slots
        result: list[SchedulingCandidate] = []
        for candidate in feature.ready:
            current = active.get((candidate.issue_number, candidate.delivery))
            if current is not None:
                candidate_identity = (
                    candidate.parent_issue,
                    candidate.issue_number,
                    candidate.delivery,
                )
                if not current.job_running and (
                    local_owner is None or candidate_identity == local_owner
                ):
                    result.append(candidate)
            elif (
                candidate.kind is not CandidateKind.START_CHILD
                or feature.active_slot_count < limit
            ) and local_owner is None:
                result.append(candidate)
        return tuple(result)

    def _local_active_owner(
        self, features: Sequence[FeatureWork]
    ) -> tuple[int, int, int] | None:
        if self.delivery_mode is DeliveryMode.CHILD_PRS:
            return None
        active = tuple(
            delivery
            for feature in features
            for delivery in feature.active
        )
        if not active:
            return None
        owner = min(
            active,
            key=lambda delivery: (
                not delivery.job_running,
                delivery.parent_issue,
                delivery.issue_number,
                delivery.delivery,
            ),
        )
        return owner.parent_issue, owner.issue_number, owner.delivery

    @staticmethod
    def _next_parent(parents: Sequence[int], last_served: int | None) -> int:
        if last_served is None:
            return parents[0]
        return next((parent for parent in parents if parent > last_served), parents[0])

    def _pause_reasons(self, features: Sequence[FeatureWork]) -> tuple[str, ...]:
        reasons: list[str] = []
        for feature in sorted(features, key=lambda item: item.parent_issue):
            limit = feature.max_active_slots or self.default_feature_slots
            if (
                feature.active_slot_count >= limit
                and all(
                    item.stage is DeliveryStage.REOPENED_PREREQUISITE
                    for item in feature.active
                )
            ):
                reasons.append(
                    f"parent {feature.parent_issue} paused: all {limit} active child "
                    "slots are occupied by deliveries blocked on reopened "
                    "prerequisites; release a slot to continue"
                )
        return tuple(reasons)


@dataclass(slots=True)
class SnapshotScheduler:
    """Refresh facts for every decision so no mutation follows a stale snapshot."""

    snapshot_reader: Callable[[], tuple[FeatureSnapshot, ...]]

    def select(self) -> NextTransition | None:
        transition = decide_next(self.snapshot_reader())
        return None if transition.kind.value == "wait" else transition


@dataclass(slots=True)
class SupervisedProcess:
    process: subprocess.Popen[bytes]
    job_object: WindowsJobObject | None


class SubprocessSupervisor:
    """Owns each launched process tree and never targets unrelated processes."""

    def __init__(self, command: Callable[[object], Sequence[str]]) -> None:
        self._command = command

    def launch(self, transition: object) -> SupervisedProcess:
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        )
        process = subprocess.Popen(
            tuple(self._command(transition)),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name != "nt",
            creationflags=creationflags,
        )
        job: WindowsJobObject | None = None
        if os.name == "nt":
            job = WindowsJobObject()
            try:
                job.assign(process_handle=int(process._handle))  # type: ignore[attr-defined]
            except BaseException:
                process.kill()
                process.wait()
                job.close()
                raise
        return SupervisedProcess(process, job)

    def wait(self, job: SupervisedProcess) -> None:
        try:
            return_code = job.process.wait()
            if return_code:
                raise RuntimeError(f"owned subprocess exited with status {return_code}")
        finally:
            if job.job_object is not None:
                job.job_object.close()

    def request_graceful_cancellation(self, job: SupervisedProcess) -> None:
        if job.process.poll() is not None:
            return
        if os.name == "nt":
            job.process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(job.process.pid, signal.SIGTERM)

    def terminate_owned_tree(self, job: SupervisedProcess) -> None:
        if job.process.poll() is not None:
            return
        if job.job_object is not None:
            job.job_object.close()
        else:
            os.killpg(job.process.pid, signal.SIGKILL)
