from __future__ import annotations

from pathlib import Path

from local_issue_runner.scheduler import (
    ActiveDelivery,
    CandidateKind,
    DeliveryMode,
    DeliveryStage,
    FeatureWork,
    SchedulingCandidate,
    SchedulingPolicy,
)


def candidate(
    parent: int,
    issue: int,
    *,
    kind: CandidateKind = CandidateKind.START_CHILD,
    delivery: int = 1,
) -> SchedulingCandidate:
    return SchedulingCandidate(
        parent_issue=parent,
        issue_number=issue,
        delivery=delivery,
        worktree=Path(f"runtime/worktrees/p-{parent}-i-{issue}-d-{delivery}"),
        kind=kind,
    )


def active(
    parent: int,
    issue: int,
    *,
    stage: DeliveryStage,
    job_running: bool = False,
    delivery: int = 1,
) -> ActiveDelivery:
    return ActiveDelivery(
        parent_issue=parent,
        issue_number=issue,
        delivery=delivery,
        worktree=Path(f"runtime/worktrees/p-{parent}-i-{issue}-d-{delivery}"),
        stage=stage,
        job_running=job_running,
    )


def test_independent_ready_children_start_in_distinct_worktrees() -> None:
    policy = SchedulingPolicy()
    first = candidate(100, 101)
    second = candidate(100, 102)

    initial = policy.select((FeatureWork(100, ready=(first, second)),))
    after_first_starts = policy.select(
        (
            FeatureWork(
                100,
                ready=(second,),
                active=(
                    active(
                        100,
                        101,
                        stage=DeliveryStage.IMPLEMENTING,
                        job_running=True,
                    ),
                ),
            ),
        )
    )

    assert initial.candidate is not None
    assert after_first_starts.candidate is not None
    assert initial.candidate == first
    assert after_first_starts.candidate == second
    assert initial.candidate.worktree != after_first_starts.candidate.worktree


def test_child_prs_preserves_parallel_progress_across_features() -> None:
    policy = SchedulingPolicy.for_delivery_mode(DeliveryMode.CHILD_PRS)
    second = candidate(200, 201)

    decision = policy.select(
        (
            FeatureWork(
                100,
                active=(
                    active(
                        100,
                        101,
                        stage=DeliveryStage.IMPLEMENTING,
                        job_running=True,
                    ),
                ),
            ),
            FeatureWork(200, ready=(second,)),
        )
    )

    assert decision.candidate == second


def test_local_commits_allows_only_one_globally_active_child() -> None:
    policy = SchedulingPolicy.for_delivery_mode(DeliveryMode.LOCAL_COMMITS)
    first = candidate(100, 101)
    second = candidate(200, 201)

    initial = policy.select(
        (FeatureWork(100, ready=(first,)), FeatureWork(200, ready=(second,)))
    )
    after_first_starts = policy.select(
        (
            FeatureWork(
                100,
                active=(
                    active(
                        100,
                        101,
                        stage=DeliveryStage.IMPLEMENTING,
                        job_running=True,
                    ),
                ),
            ),
            FeatureWork(200, ready=(second,)),
        )
    )

    assert initial.candidate == first
    assert after_first_starts.candidate is None


def test_local_commits_reserves_global_slot_between_child_transitions() -> None:
    policy = SchedulingPolicy.for_delivery_mode(DeliveryMode.LOCAL_COMMITS)

    decision = policy.select(
        (
            FeatureWork(
                100,
                active=(
                    active(
                        100,
                        101,
                        stage=DeliveryStage.WAITING_PR,
                    ),
                ),
            ),
            FeatureWork(200, ready=(candidate(200, 201),)),
        )
    )

    assert decision.candidate is None


def test_local_commits_can_continue_the_globally_active_child() -> None:
    policy = SchedulingPolicy.for_delivery_mode(DeliveryMode.LOCAL_COMMITS)
    repair = candidate(100, 101, kind=CandidateKind.RECOVERY)

    decision = policy.select(
        (
            FeatureWork(
                100,
                ready=(repair,),
                active=(
                    active(
                        100,
                        101,
                        stage=DeliveryStage.RECOVERING,
                    ),
                ),
            ),
            FeatureWork(200, ready=(candidate(200, 201),)),
        )
    )

    assert decision.candidate == repair


def test_delivery_with_a_running_coding_job_cannot_start_another_job() -> None:
    policy = SchedulingPolicy()
    repair = candidate(100, 101, kind=CandidateKind.ACTIONABLE_PR)

    decision = policy.select(
        (
            FeatureWork(
                100,
                ready=(repair,),
                active=(
                    active(
                        100,
                        101,
                        stage=DeliveryStage.ACTIONABLE_PR,
                        job_running=True,
                    ),
                ),
            ),
        )
    )

    assert decision.candidate is None
    assert decision.pause_reasons == ()


def test_default_feature_capacity_is_five_active_child_slots() -> None:
    policy = SchedulingPolicy()
    occupied = tuple(
        active(100, issue, stage=DeliveryStage.WAITING_PR)
        for issue in range(101, 106)
    )

    decision = policy.select(
        (FeatureWork(100, ready=(candidate(100, 106),), active=occupied),)
    )

    assert policy.default_feature_slots == 5
    assert decision.candidate is None


def test_waiting_pr_uses_a_slot_but_repair_reuses_it_without_an_extra_job() -> None:
    policy = SchedulingPolicy()
    waiting = active(100, 101, stage=DeliveryStage.WAITING_PR)
    other_slots = tuple(
        active(100, issue, stage=DeliveryStage.WAITING_PR)
        for issue in range(102, 106)
    )
    repair = candidate(100, 101, kind=CandidateKind.ACTIONABLE_PR)

    decision = policy.select(
        (
            FeatureWork(
                100,
                ready=(candidate(100, 106), repair),
                active=(waiting, *other_slots),
            ),
        )
    )

    assert waiting.job_running is False
    assert decision.candidate == repair


def test_round_robin_fairness_serves_parent_after_last_served_parent() -> None:
    policy = SchedulingPolicy()
    parent_100 = FeatureWork(100, ready=(candidate(100, 101),))
    parent_200 = FeatureWork(200, ready=(candidate(200, 201),))

    first = policy.select((parent_100, parent_200), last_served_parent=100)
    second = policy.select((parent_100, parent_200), last_served_parent=200)

    assert first.candidate == candidate(200, 201)
    assert second.candidate == candidate(100, 101)


def test_actionable_runner_owned_pr_has_priority_over_fresh_work() -> None:
    policy = SchedulingPolicy()
    fresh = candidate(100, 101)
    repair = candidate(200, 201, kind=CandidateKind.ACTIONABLE_PR)

    decision = policy.select(
        (
            FeatureWork(100, ready=(fresh,)),
            FeatureWork(
                200,
                ready=(repair,),
                active=(active(200, 201, stage=DeliveryStage.ACTIONABLE_PR),),
            ),
        ),
        last_served_parent=200,
    )

    assert decision.candidate == repair


def test_blocked_feature_does_not_prevent_another_feature_progressing() -> None:
    policy = SchedulingPolicy()
    blocked = tuple(
        active(100, issue, stage=DeliveryStage.REOPENED_PREREQUISITE)
        for issue in range(101, 106)
    )
    available = candidate(200, 201)

    decision = policy.select(
        (
            FeatureWork(100, active=blocked),
            FeatureWork(200, ready=(available,)),
        )
    )

    assert decision.candidate == available


def test_reopened_prerequisites_filling_all_slots_report_explicit_pause() -> None:
    policy = SchedulingPolicy()
    blocked = tuple(
        active(100, issue, stage=DeliveryStage.REOPENED_PREREQUISITE)
        for issue in range(101, 106)
    )

    decision = policy.select((FeatureWork(100, active=blocked),))

    assert decision.candidate is None
    assert decision.pause_reasons == (
        (
            "parent 100 paused: all 5 active child slots are occupied by "
            "deliveries blocked on reopened prerequisites; release a slot to continue"
        ),
    )
