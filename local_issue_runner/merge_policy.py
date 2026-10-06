from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from local_issue_runner.models import AutonomyLevel


class MergeTarget(StrEnum):
    CHILD = "child"
    PARENT = "parent"


class MergeBlockerCode(StrEnum):
    CHILD_AUTONOMY_REQUIRED = "child_autonomy_required"
    PARENT_AUTONOMY_REQUIRED = "parent_autonomy_required"
    QUALIFICATION_REQUIRED = "qualification_required"
    PR_NOT_OPEN = "pr_not_open"
    PR_IS_DRAFT = "pr_is_draft"
    PR_NOT_RUNNER_OWNED = "pr_not_runner_owned"
    TARGET_BRANCH_MISMATCH = "target_branch_mismatch"
    SCOPE_STALE = "scope_stale"
    ACTIVE_CODING_JOB = "active_coding_job"
    VALIDATION_HEAD_STALE = "validation_head_stale"
    VALIDATION_BASE_STALE = "validation_base_stale"
    VALIDATION_CONFIG_STALE = "validation_config_stale"
    LOCAL_VALIDATION_FAILED = "local_validation_failed"
    BASE_OUT_OF_DATE = "base_out_of_date"
    PROTECTION_RULES_UNREADABLE = "protection_rules_unreadable"
    STRICT_PROTECTION_REQUIRED = "strict_protection_required"
    REAL_REQUIRED_CHECK_MISSING = "real_required_check_missing"
    REQUIRED_CHECKS_NOT_PASSED = "required_checks_not_passed"
    APPROVAL_RULES_UNREADABLE = "approval_rules_unreadable"
    APPROVALS_MISSING = "approvals_missing"
    CONVERSATIONS_UNRESOLVED = "conversations_unresolved"
    FEEDBACK_UNACCOUNTED = "feedback_unaccounted"
    QUIET_PERIOD_ACTIVE = "quiet_period_active"
    HUMAN_CHANGES_REQUESTED = "human_changes_requested"
    HUMAN_THREAD_UNRESOLVED = "human_thread_unresolved"
    MERGE_CONFLICT = "merge_conflict"
    ACTOR_EXEMPT = "actor_exempt"


@dataclass(frozen=True, slots=True)
class MergeReadinessFacts:
    target: MergeTarget
    autonomy: AutonomyLevel
    head_sha: str
    current_base_sha: str
    validated_head_sha: str | None
    validated_base_sha: str | None
    validation_config_fingerprint: str | None
    current_validation_config_fingerprint: str
    local_validation_passed: bool
    base_up_to_date: bool
    rules_readable: bool
    strict_protection: bool
    required_checks: tuple[str, ...]
    passing_required_checks: tuple[str, ...]
    approval_rules_readable: bool
    required_approvals: int
    current_approvals: int
    conversation_resolution_required: bool
    conversations_resolved: bool
    quiet_period_started_at: datetime
    quiet_period: timedelta
    unresolved_human_changes_requested: bool
    actor_exempt: bool
    pr_open: bool = True
    pr_draft: bool = False
    runner_owned: bool = True
    target_branch_matches: bool = True
    scope_current: bool = True
    active_coding_job: bool = False
    feedback_accounted_for: bool = True
    unresolved_human_thread: bool = False
    merge_conflict: bool = False
    qualification_valid: bool = True

    def __post_init__(self) -> None:
        if not self.head_sha or not self.current_base_sha:
            raise ValueError("current head and base SHAs are required")
        if not self.current_validation_config_fingerprint:
            raise ValueError("current validation configuration is required")
        if self.required_approvals < 0 or self.current_approvals < 0:
            raise ValueError("approval counts cannot be negative")
        if self.quiet_period < timedelta(0):
            raise ValueError("quiet period cannot be negative")
        if len(self.required_checks) != len(set(self.required_checks)):
            raise ValueError("required checks must be unique")
        if len(self.passing_required_checks) != len(
            set(self.passing_required_checks)
        ):
            raise ValueError("passing required checks must be unique")


@dataclass(frozen=True, slots=True)
class MergeBlocker:
    target: MergeTarget
    code: MergeBlockerCode
    detail: str
    retry_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MergeReadinessDecision:
    target: MergeTarget
    blockers: tuple[MergeBlocker, ...]

    @property
    def eligible(self) -> bool:
        return not self.blockers


class MergeExecutionBlocked(RuntimeError):
    """A merge decision cannot authorize the requested mutation."""


def require_merge_authorization(
    decision: MergeReadinessDecision, target: MergeTarget
) -> None:
    """Reject stale or inapplicable readiness decisions before mutation."""
    if decision.target is not target:
        raise MergeExecutionBlocked(
            f"merge readiness is for {decision.target.value}, not {target.value}"
        )
    if decision.blockers:
        codes = ", ".join(blocker.code.value for blocker in decision.blockers)
        raise MergeExecutionBlocked(f"merge readiness is blocked: {codes}")


class MergeReadinessPolicy:
    """Pure evaluation of freshly observed merge facts."""

    def __init__(self, *, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(timezone.utc))

    def evaluate(self, facts: MergeReadinessFacts) -> MergeReadinessDecision:
        blockers: list[MergeBlocker] = []

        def block(
            code: MergeBlockerCode,
            detail: str,
            retry_at: datetime | None = None,
        ) -> None:
            blockers.append(MergeBlocker(facts.target, code, detail, retry_at))

        if facts.target is MergeTarget.CHILD:
            if facts.autonomy not in {
                AutonomyLevel.MERGE_CHILDREN,
                AutonomyLevel.MERGE_PARENTS,
            }:
                block(
                    MergeBlockerCode.CHILD_AUTONOMY_REQUIRED,
                    "autonomy must permit child merges",
                )
        elif facts.autonomy is not AutonomyLevel.MERGE_PARENTS:
            block(
                MergeBlockerCode.PARENT_AUTONOMY_REQUIRED,
                "autonomy must permit parent merges",
            )
        if not facts.qualification_valid:
            block(
                MergeBlockerCode.QUALIFICATION_REQUIRED,
                "a current live-qualification receipt is required",
            )

        simple_gates = (
            (not facts.pr_open, MergeBlockerCode.PR_NOT_OPEN, "pull request is not open"),
            (facts.pr_draft, MergeBlockerCode.PR_IS_DRAFT, "pull request is a draft"),
            (
                not facts.runner_owned,
                MergeBlockerCode.PR_NOT_RUNNER_OWNED,
                "pull request was not created by this runner",
            ),
            (
                not facts.target_branch_matches,
                MergeBlockerCode.TARGET_BRANCH_MISMATCH,
                "pull request target branch does not match configuration",
            ),
            (
                not facts.scope_current,
                MergeBlockerCode.SCOPE_STALE,
                "delivery or requirement scope is stale",
            ),
            (
                facts.active_coding_job,
                MergeBlockerCode.ACTIVE_CODING_JOB,
                "a coding job is active for this delivery",
            ),
        )
        for failed, code, detail in simple_gates:
            if failed:
                block(code, detail)

        if facts.validated_head_sha != facts.head_sha:
            block(
                MergeBlockerCode.VALIDATION_HEAD_STALE,
                "local validation does not match the current head",
            )
        if facts.validated_base_sha != facts.current_base_sha:
            block(
                MergeBlockerCode.VALIDATION_BASE_STALE,
                "local validation does not match the current base",
            )
        if (
            facts.validation_config_fingerprint
            != facts.current_validation_config_fingerprint
        ):
            block(
                MergeBlockerCode.VALIDATION_CONFIG_STALE,
                "local validation configuration is stale",
            )
        if not facts.local_validation_passed:
            block(
                MergeBlockerCode.LOCAL_VALIDATION_FAILED,
                "local validation did not pass",
            )
        if not facts.base_up_to_date:
            block(
                MergeBlockerCode.BASE_OUT_OF_DATE,
                "the latest base has not been incorporated",
            )
        if not facts.rules_readable:
            block(
                MergeBlockerCode.PROTECTION_RULES_UNREADABLE,
                "effective branch protection rules are unreadable",
            )
        elif not facts.strict_protection:
            block(
                MergeBlockerCode.STRICT_PROTECTION_REQUIRED,
                "strict up-to-date branch protection is required",
            )
        if not facts.required_checks:
            block(
                MergeBlockerCode.REAL_REQUIRED_CHECK_MISSING,
                "at least one real required check is required",
            )
        else:
            missing = tuple(
                name
                for name in facts.required_checks
                if name not in set(facts.passing_required_checks)
            )
            if missing:
                label = "check" if len(missing) == 1 else "checks"
                block(
                    MergeBlockerCode.REQUIRED_CHECKS_NOT_PASSED,
                    f"required {label} not passing: {', '.join(missing)}",
                )
        if not facts.approval_rules_readable:
            block(
                MergeBlockerCode.APPROVAL_RULES_UNREADABLE,
                "applicable approval rules are unreadable",
            )
        elif facts.current_approvals < facts.required_approvals:
            block(
                MergeBlockerCode.APPROVALS_MISSING,
                f"{facts.required_approvals - facts.current_approvals} required "
                "approval(s) missing",
            )
        if (
            facts.conversation_resolution_required
            and not facts.conversations_resolved
        ):
            block(
                MergeBlockerCode.CONVERSATIONS_UNRESOLVED,
                "GitHub requires all conversations to be resolved",
            )
        if not facts.feedback_accounted_for:
            block(
                MergeBlockerCode.FEEDBACK_UNACCOUNTED,
                "external feedback lacks a verified response or disposition",
            )
        quiet_until = facts.quiet_period_started_at + facts.quiet_period
        if self._now() < quiet_until:
            block(
                MergeBlockerCode.QUIET_PERIOD_ACTIVE,
                "merge quiet period has not elapsed",
                quiet_until,
            )
        if facts.unresolved_human_changes_requested:
            block(
                MergeBlockerCode.HUMAN_CHANGES_REQUESTED,
                "a human request for changes remains unresolved",
            )
        if facts.unresolved_human_thread:
            block(
                MergeBlockerCode.HUMAN_THREAD_UNRESOLVED,
                "a human review thread remains unresolved",
            )
        if facts.merge_conflict:
            block(MergeBlockerCode.MERGE_CONFLICT, "pull request has a merge conflict")
        if facts.actor_exempt:
            block(
                MergeBlockerCode.ACTOR_EXEMPT,
                "authenticated actor is exempt from required protection",
            )
        return MergeReadinessDecision(facts.target, tuple(blockers))
