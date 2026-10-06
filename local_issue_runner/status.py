from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from local_issue_runner.merge_policy import (
    MergeBlocker,
    MergeBlockerCode,
    MergeReadinessDecision,
)


class MergeGate(StrEnum):
    AUTONOMY = "autonomy"
    QUALIFICATION = "qualification"
    PULL_REQUEST = "pull_request"
    SCOPE = "scope"
    VALIDATION = "validation"
    BASE = "base"
    PROTECTION = "protection"
    CHECKS = "checks"
    APPROVALS = "approvals"
    FEEDBACK = "feedback"
    QUIET_PERIOD = "quiet_period"
    MERGEABILITY = "mergeability"


_GATES: dict[MergeBlockerCode, MergeGate] = {
    MergeBlockerCode.CHILD_AUTONOMY_REQUIRED: MergeGate.AUTONOMY,
    MergeBlockerCode.PARENT_AUTONOMY_REQUIRED: MergeGate.AUTONOMY,
    MergeBlockerCode.QUALIFICATION_REQUIRED: MergeGate.QUALIFICATION,
    MergeBlockerCode.PR_NOT_OPEN: MergeGate.PULL_REQUEST,
    MergeBlockerCode.PR_IS_DRAFT: MergeGate.PULL_REQUEST,
    MergeBlockerCode.PR_NOT_RUNNER_OWNED: MergeGate.PULL_REQUEST,
    MergeBlockerCode.TARGET_BRANCH_MISMATCH: MergeGate.PULL_REQUEST,
    MergeBlockerCode.SCOPE_STALE: MergeGate.SCOPE,
    MergeBlockerCode.ACTIVE_CODING_JOB: MergeGate.SCOPE,
    MergeBlockerCode.VALIDATION_HEAD_STALE: MergeGate.VALIDATION,
    MergeBlockerCode.VALIDATION_BASE_STALE: MergeGate.VALIDATION,
    MergeBlockerCode.VALIDATION_CONFIG_STALE: MergeGate.VALIDATION,
    MergeBlockerCode.LOCAL_VALIDATION_FAILED: MergeGate.VALIDATION,
    MergeBlockerCode.BASE_OUT_OF_DATE: MergeGate.BASE,
    MergeBlockerCode.PROTECTION_RULES_UNREADABLE: MergeGate.PROTECTION,
    MergeBlockerCode.STRICT_PROTECTION_REQUIRED: MergeGate.PROTECTION,
    MergeBlockerCode.REAL_REQUIRED_CHECK_MISSING: MergeGate.CHECKS,
    MergeBlockerCode.REQUIRED_CHECKS_NOT_PASSED: MergeGate.CHECKS,
    MergeBlockerCode.APPROVAL_RULES_UNREADABLE: MergeGate.APPROVALS,
    MergeBlockerCode.APPROVALS_MISSING: MergeGate.APPROVALS,
    MergeBlockerCode.CONVERSATIONS_UNRESOLVED: MergeGate.APPROVALS,
    MergeBlockerCode.FEEDBACK_UNACCOUNTED: MergeGate.FEEDBACK,
    MergeBlockerCode.QUIET_PERIOD_ACTIVE: MergeGate.QUIET_PERIOD,
    MergeBlockerCode.HUMAN_CHANGES_REQUESTED: MergeGate.FEEDBACK,
    MergeBlockerCode.HUMAN_THREAD_UNRESOLVED: MergeGate.FEEDBACK,
    MergeBlockerCode.MERGE_CONFLICT: MergeGate.MERGEABILITY,
    MergeBlockerCode.ACTOR_EXEMPT: MergeGate.PROTECTION,
}

_QUALIFICATION_BLOCKER_CODE = "qualification_required"
_QUALIFICATION_BLOCKER_DETAIL = "a valid live-qualification receipt is required"


def _gate_for(blocker: MergeBlocker) -> MergeGate:
    return _GATES[blocker.code]


def _only_blocking_gate(
    blockers: tuple[MergeBlocker, ...],
) -> MergeGate | None:
    if not blockers:
        return None
    first_gate = _gate_for(blockers[0])
    if all(_gate_for(blocker) is first_gate for blocker in blockers):
        return first_gate
    return None


def _format_blocker(
    code: str,
    detail: str,
    *,
    gate: MergeGate | None = None,
    retry_at: str | None = None,
) -> str:
    gate_text = f" gate={gate.value}" if gate is not None else ""
    retry_text = f"; retry_at={retry_at}" if retry_at is not None else ""
    return f"  BLOCKER {code}{gate_text}: {detail}{retry_text}"


@dataclass(frozen=True, slots=True)
class MergeEligibilityStatus:
    identity: str
    decision: MergeReadinessDecision
    qualification_valid: bool

    def __post_init__(self) -> None:
        if not self.identity.strip():
            raise ValueError("merge status identity is required")

    @property
    def eligible(self) -> bool:
        return self.decision.eligible and self.qualification_valid

    @property
    def waiting_gate(self) -> MergeGate | None:
        if self.decision.blockers:
            return _gate_for(self.decision.blockers[0])
        if not self.qualification_valid:
            return MergeGate.QUALIFICATION
        return None

    @property
    def only_remaining_blocker(self) -> MergeGate | None:
        if not self.qualification_valid:
            return (
                MergeGate.QUALIFICATION
                if not self.decision.blockers
                else None
            )
        only_gate = _only_blocking_gate(self.decision.blockers)
        return (
            only_gate
            if only_gate in {MergeGate.AUTONOMY, MergeGate.QUALIFICATION}
            else None
        )


def render_merge_eligibility(status: MergeEligibilityStatus) -> tuple[str, ...]:
    target = status.decision.target.value
    if status.eligible:
        return (f"MERGE-ELIGIBLE {target} {status.identity}: yes; waiting=none",)

    waiting = status.waiting_gate
    only = status.only_remaining_blocker
    summary = (
        f"MERGE-ELIGIBLE {target} {status.identity}: no; "
        f"waiting={waiting.value if waiting else 'unknown'}; "
        f"only_remaining={only.value if only else 'no'}"
    )
    details = tuple(_render_blocker(blocker) for blocker in status.decision.blockers)
    if (
        not status.qualification_valid
        and all(
            blocker.code is not MergeBlockerCode.QUALIFICATION_REQUIRED
            for blocker in status.decision.blockers
        )
    ):
        details += (
            _format_blocker(
                _QUALIFICATION_BLOCKER_CODE,
                _QUALIFICATION_BLOCKER_DETAIL,
            ),
        )
    return (summary, *details)


def _render_blocker(blocker: MergeBlocker) -> str:
    return _format_blocker(
        blocker.code.value,
        blocker.detail,
        gate=_gate_for(blocker),
        retry_at=(
            blocker.retry_at.isoformat()
            if blocker.retry_at is not None
            else None
        ),
    )
