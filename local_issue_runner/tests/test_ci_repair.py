from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from local_issue_runner.checks import (
    CheckConclusion,
    CheckObservation,
    CheckState,
    CIRepairPolicy,
    CIRepairStore,
    FailureCategory,
    FailureObservation,
    RepairAction,
    problem_identity,
)

NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
HEAD = "a" * 40


def check(
    name: str,
    *,
    state: CheckState = CheckState.COMPLETED,
    conclusion: CheckConclusion | None = CheckConclusion.SUCCESS,
) -> CheckObservation:
    return CheckObservation(
        name=name,
        state=state,
        conclusion=conclusion,
        source="check_run",
    )


def policy(tmp_path: Path, *, now: datetime = NOW) -> CIRepairPolicy:
    return CIRepairPolicy(
        required_checks=("unit", "lint"),
        store=CIRepairStore(tmp_path / "runner.db"),
        ci_wait_timeout=timedelta(hours=1),
        now=lambda: now,
    )


def test_only_required_checks_gate_progress(tmp_path: Path) -> None:
    decision = policy(tmp_path).evaluate_checks(
        head_sha=HEAD,
        checks=(
            check("unit"),
            check("lint"),
            check("optional-security", conclusion=CheckConclusion.FAILURE),
        ),
    )

    assert decision.state is CheckState.PASSED
    assert decision.blocking_checks == ()
    assert decision.optional_checks == ("optional-security",)
    assert decision.repair_action is RepairAction.NONE


def test_missing_required_check_waits_instead_of_passing(tmp_path: Path) -> None:
    decision = policy(tmp_path).evaluate_checks(
        head_sha=HEAD,
        checks=(check("unit"),),
    )

    assert decision.state is CheckState.WAITING
    assert decision.blocking_checks == ("lint",)
    assert decision.wait_deadline == NOW + timedelta(hours=1)


@pytest.mark.parametrize(
    "conclusion",
    [
        CheckConclusion.FAILURE,
        CheckConclusion.CANCELLED,
        CheckConclusion.TIMED_OUT,
    ],
)
def test_terminal_required_check_failures_block_progress(
    tmp_path: Path, conclusion: CheckConclusion
) -> None:
    decision = policy(tmp_path).evaluate_checks(
        head_sha=HEAD,
        checks=(check("unit", conclusion=conclusion), check("lint")),
    )

    assert decision.state is CheckState.BLOCKED
    assert decision.blocking_checks == ("unit",)


def test_ci_wait_deadline_survives_restart_and_late_success_resumes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runner.db"
    first = CIRepairPolicy(
        required_checks=("unit",),
        store=CIRepairStore(database),
        ci_wait_timeout=timedelta(hours=1),
        now=lambda: NOW,
    )
    initial = first.evaluate_checks(head_sha=HEAD, checks=())

    restarted = CIRepairPolicy(
        required_checks=("unit",),
        store=CIRepairStore(database),
        ci_wait_timeout=timedelta(hours=1),
        now=lambda: NOW + timedelta(minutes=50),
    )
    waiting = restarted.evaluate_checks(head_sha=HEAD, checks=())
    timed_out = CIRepairPolicy(
        required_checks=("unit",),
        store=CIRepairStore(database),
        ci_wait_timeout=timedelta(hours=1),
        now=lambda: NOW + timedelta(hours=2),
    ).evaluate_checks(head_sha=HEAD, checks=())
    recovered = restarted.evaluate_checks(
        head_sha=HEAD, checks=(check("unit"),)
    )

    assert waiting.wait_deadline == initial.wait_deadline
    assert timed_out.state is CheckState.NEEDS_HUMAN
    assert recovered.state is CheckState.PASSED


@pytest.mark.parametrize(
    "category",
    [FailureCategory.CODE, FailureCategory.LOCAL_VALIDATION],
)
def test_actionable_failures_enter_repair_jobs(
    tmp_path: Path, category: FailureCategory
) -> None:
    failure = FailureObservation(
        identity="pytest:test_widget",
        category=category,
        detail="Assertion failed in changed code",
    )

    decision = policy(tmp_path).plan_failure(failure)

    assert decision.action is RepairAction.START_REPAIR
    assert decision.failure_identity == failure.identity


@pytest.mark.parametrize(
    "category",
    [
        FailureCategory.CREDENTIALS,
        FailureCategory.UNRELATED,
        FailureCategory.UNCLEAR_REQUIREMENT,
    ],
)
def test_non_actionable_failures_require_a_human(
    tmp_path: Path, category: FailureCategory
) -> None:
    decision = policy(tmp_path).plan_failure(
        FailureObservation(
            identity=f"{category.value}:failure",
            category=category,
            detail="Not safely repairable by changing product code",
        )
    )

    assert decision.action is RepairAction.NEEDS_HUMAN
    assert decision.reason


def test_infrastructure_retries_wait_one_then_five_minutes_before_needs_human(
    tmp_path: Path,
) -> None:
    subject = policy(tmp_path)
    failure = FailureObservation(
        identity="github:service-unavailable",
        category=FailureCategory.INFRASTRUCTURE,
        detail="GitHub returned a transient 503 response",
    )

    first = subject.plan_failure(failure, infrastructure_attempt=1)
    second = subject.plan_failure(failure, infrastructure_attempt=2)
    exhausted = subject.plan_failure(failure, infrastructure_attempt=3)

    assert first.action is RepairAction.WAIT
    assert first.resume_at == NOW + timedelta(minutes=1)
    assert second.action is RepairAction.WAIT
    assert second.resume_at == NOW + timedelta(minutes=5)
    assert exhausted.action is RepairAction.NEEDS_HUMAN


def test_rate_limit_waits_until_reset_without_zero_delay_polling(
    tmp_path: Path,
) -> None:
    reset = NOW + timedelta(minutes=7)

    decision = policy(tmp_path).rate_limit_wait(reset_at=reset)

    assert decision.action is RepairAction.WAIT
    assert decision.resume_at == reset
    assert decision.resume_at is not None
    assert decision.resume_at > NOW


def test_problem_identity_uses_only_stable_canonical_components() -> None:
    assert problem_identity(" required-check ", " unit ", " test_widget ") == (
        "required-check:unit:test_widget"
    )
    with pytest.raises(ValueError, match="must not be empty"):
        problem_identity("required-check", " ")


def test_no_progress_streak_survives_restarts_deliveries_and_repair_children(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runner.db"
    problem = "required-check:unit:test_widget"
    first = CIRepairPolicy(
        required_checks=("unit",),
        store=CIRepairStore(database),
        ci_wait_timeout=timedelta(hours=1),
        now=lambda: NOW,
    )
    attempt_one = first.record_no_progress(
        problem_identity=problem,
        delivery=1,
        repair_child=None,
    )
    restarted = CIRepairPolicy(
        required_checks=("unit",),
        store=CIRepairStore(database),
        ci_wait_timeout=timedelta(hours=1),
        now=lambda: NOW + timedelta(minutes=10),
    )
    attempt_two = restarted.record_no_progress(
        problem_identity=problem,
        delivery=2,
        repair_child=901,
    )

    assert attempt_one.streak == 1
    assert attempt_two.streak == 2
    assert attempt_two.action is RepairAction.NEEDS_HUMAN
    assert attempt_two.stopped is True


def test_explicit_retry_resets_stopped_streak_and_records_reason(
    tmp_path: Path,
) -> None:
    subject = policy(tmp_path)
    problem = "required-check:unit:test_widget"
    subject.record_no_progress(
        problem_identity=problem, delivery=1, repair_child=None
    )
    subject.record_no_progress(
        problem_identity=problem, delivery=2, repair_child=901
    )

    reset = subject.retry(
        problem_identity=problem,
        reason="Reviewed logs; the external service has recovered.",
    )
    history = subject.store.problem(problem)

    assert reset.streak == 0
    assert reset.stopped is False
    assert history.retry_reason == (
        "Reviewed logs; the external service has recovered."
    )
