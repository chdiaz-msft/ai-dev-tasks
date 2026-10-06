from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from local_issue_runner.merge_policy import (
    MergeBlockerCode,
    MergeReadinessFacts,
    MergeReadinessPolicy,
    MergeTarget,
)
from local_issue_runner.models import AutonomyLevel
from local_issue_runner.status import MergeEligibilityStatus, render_merge_eligibility

HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
VALIDATION_CONFIG = "c" * 64
NOW = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)


def ready_facts(*, target: MergeTarget = MergeTarget.CHILD) -> MergeReadinessFacts:
    return MergeReadinessFacts(
        target=target,
        autonomy=(
            AutonomyLevel.MERGE_CHILDREN
            if target is MergeTarget.CHILD
            else AutonomyLevel.MERGE_PARENTS
        ),
        head_sha=HEAD_SHA,
        current_base_sha=BASE_SHA,
        validated_head_sha=HEAD_SHA,
        validated_base_sha=BASE_SHA,
        validation_config_fingerprint=VALIDATION_CONFIG,
        current_validation_config_fingerprint=VALIDATION_CONFIG,
        local_validation_passed=True,
        base_up_to_date=True,
        rules_readable=True,
        strict_protection=True,
        required_checks=("unit",),
        passing_required_checks=("unit",),
        approval_rules_readable=True,
        required_approvals=1,
        current_approvals=1,
        conversation_resolution_required=True,
        conversations_resolved=True,
        quiet_period_started_at=NOW - timedelta(hours=2),
        quiet_period=timedelta(hours=1),
        unresolved_human_changes_requested=False,
        actor_exempt=False,
    )


def blocker_codes(facts: MergeReadinessFacts) -> tuple[MergeBlockerCode, ...]:
    return tuple(
        blocker.code
        for blocker in MergeReadinessPolicy(now=lambda: NOW).evaluate(facts).blockers
    )


@pytest.mark.parametrize("target", [MergeTarget.CHILD, MergeTarget.PARENT])
def test_create_pr_autonomy_never_selects_a_merge(target: MergeTarget) -> None:
    facts = replace(
        ready_facts(target=target),
        autonomy=AutonomyLevel.CREATE_PR,
    )

    decision = MergeReadinessPolicy().evaluate(facts)

    expected = (
        MergeBlockerCode.CHILD_AUTONOMY_REQUIRED
        if target is MergeTarget.CHILD
        else MergeBlockerCode.PARENT_AUTONOMY_REQUIRED
    )
    assert decision.eligible is False
    assert blocker_codes(facts) == (expected,)
    assert decision.blockers[0].target is target


def test_merge_children_autonomy_does_not_select_a_parent_merge() -> None:
    facts = replace(
        ready_facts(target=MergeTarget.PARENT),
        autonomy=AutonomyLevel.MERGE_CHILDREN,
    )

    assert blocker_codes(facts) == (
        MergeBlockerCode.PARENT_AUTONOMY_REQUIRED,
    )


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"validated_head_sha": "d" * 40}, MergeBlockerCode.VALIDATION_HEAD_STALE),
        (
            {"validated_base_sha": "d" * 40},
            MergeBlockerCode.VALIDATION_BASE_STALE,
        ),
        (
            {"validation_config_fingerprint": "d" * 64},
            MergeBlockerCode.VALIDATION_CONFIG_STALE,
        ),
        (
            {"local_validation_passed": False},
            MergeBlockerCode.LOCAL_VALIDATION_FAILED,
        ),
    ],
)
def test_local_validation_must_match_the_exact_head_base_and_configuration(
    changes: dict[str, object],
    code: MergeBlockerCode,
) -> None:
    facts = replace(ready_facts(), **changes)

    assert blocker_codes(facts) == (code,)


def test_latest_base_must_already_be_incorporated() -> None:
    facts = replace(ready_facts(), base_up_to_date=False)

    assert blocker_codes(facts) == (MergeBlockerCode.BASE_OUT_OF_DATE,)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"rules_readable": False}, MergeBlockerCode.PROTECTION_RULES_UNREADABLE),
        ({"strict_protection": False}, MergeBlockerCode.STRICT_PROTECTION_REQUIRED),
        ({"required_checks": ()}, MergeBlockerCode.REAL_REQUIRED_CHECK_MISSING),
        (
            {"passing_required_checks": ()},
            MergeBlockerCode.REQUIRED_CHECKS_NOT_PASSED,
        ),
    ],
)
def test_strict_readable_protection_and_a_real_passing_required_check_are_mandatory(
    changes: dict[str, object],
    code: MergeBlockerCode,
) -> None:
    facts = replace(ready_facts(), **changes)

    assert blocker_codes(facts) == (code,)


def test_all_applicable_required_checks_must_pass() -> None:
    facts = replace(
        ready_facts(),
        required_checks=("unit", "lint"),
        passing_required_checks=("unit",),
    )

    decision = MergeReadinessPolicy().evaluate(facts)

    assert blocker_codes(facts) == (MergeBlockerCode.REQUIRED_CHECKS_NOT_PASSED,)
    assert decision.blockers[0].detail == "required check not passing: lint"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        (
            {"approval_rules_readable": False},
            MergeBlockerCode.APPROVAL_RULES_UNREADABLE,
        ),
        ({"current_approvals": 0}, MergeBlockerCode.APPROVALS_MISSING),
        (
            {"conversations_resolved": False},
            MergeBlockerCode.CONVERSATIONS_UNRESOLVED,
        ),
    ],
)
def test_applicable_github_approval_and_conversation_rules_are_enforced(
    changes: dict[str, object],
    code: MergeBlockerCode,
) -> None:
    facts = replace(ready_facts(), **changes)

    assert blocker_codes(facts) == (code,)


def test_no_approval_is_invented_when_github_requires_none() -> None:
    facts = replace(ready_facts(), required_approvals=0, current_approvals=0)

    assert MergeReadinessPolicy().evaluate(facts).eligible is True


def test_conversation_resolution_is_not_invented_when_not_applicable() -> None:
    facts = replace(
        ready_facts(),
        conversation_resolution_required=False,
        conversations_resolved=False,
    )

    assert MergeReadinessPolicy().evaluate(facts).eligible is True


def test_quiet_period_must_have_elapsed_from_its_latest_restart() -> None:
    facts = replace(
        ready_facts(),
        quiet_period_started_at=NOW - timedelta(minutes=59),
    )

    decision = MergeReadinessPolicy(now=lambda: NOW).evaluate(facts)

    assert blocker_codes_with_policy(facts, MergeReadinessPolicy(now=lambda: NOW)) == (
        MergeBlockerCode.QUIET_PERIOD_ACTIVE,
    )
    assert decision.blockers[0].retry_at == NOW + timedelta(minutes=1)


def blocker_codes_with_policy(
    facts: MergeReadinessFacts, policy: MergeReadinessPolicy
) -> tuple[MergeBlockerCode, ...]:
    return tuple(blocker.code for blocker in policy.evaluate(facts).blockers)


def test_unresolved_human_request_for_changes_blocks_merge() -> None:
    facts = replace(ready_facts(), unresolved_human_changes_requested=True)

    assert blocker_codes(facts) == (
        MergeBlockerCode.HUMAN_CHANGES_REQUESTED,
    )


def test_actor_exemption_blocks_merge_even_when_all_rules_are_satisfied() -> None:
    facts = replace(ready_facts(), actor_exempt=True)

    assert blocker_codes(facts) == (MergeBlockerCode.ACTOR_EXEMPT,)


def test_child_and_parent_blockers_have_precise_target_and_reason_codes() -> None:
    child = replace(
        ready_facts(target=MergeTarget.CHILD),
        base_up_to_date=False,
        unresolved_human_changes_requested=True,
    )
    parent = replace(
        ready_facts(target=MergeTarget.PARENT),
        rules_readable=False,
        actor_exempt=True,
    )

    child_decision = MergeReadinessPolicy().evaluate(child)
    parent_decision = MergeReadinessPolicy().evaluate(parent)

    assert [(blocker.target, blocker.code) for blocker in child_decision.blockers] == [
        (MergeTarget.CHILD, MergeBlockerCode.BASE_OUT_OF_DATE),
        (MergeTarget.CHILD, MergeBlockerCode.HUMAN_CHANGES_REQUESTED),
    ]
    assert [(blocker.target, blocker.code) for blocker in parent_decision.blockers] == [
        (MergeTarget.PARENT, MergeBlockerCode.PROTECTION_RULES_UNREADABLE),
        (MergeTarget.PARENT, MergeBlockerCode.ACTOR_EXEMPT),
    ]


@pytest.mark.parametrize("target", [MergeTarget.CHILD, MergeTarget.PARENT])
def test_all_green_facts_are_merge_eligible_without_executing_a_merge(
    target: MergeTarget,
) -> None:
    decision = MergeReadinessPolicy(now=lambda: NOW).evaluate(
        ready_facts(target=target)
    )

    assert decision.eligible is True
    assert decision.blockers == ()


def test_status_explains_waiting_gate_and_autonomy_only_blocker() -> None:
    decision = MergeReadinessPolicy(now=lambda: NOW).evaluate(
        replace(ready_facts(), autonomy=AutonomyLevel.CREATE_PR)
    )

    output = render_merge_eligibility(
        MergeEligibilityStatus("PR #42", decision, qualification_valid=True)
    )

    assert output[0] == (
        "MERGE-ELIGIBLE child PR #42: no; "
        "waiting=autonomy; only_remaining=autonomy"
    )
    assert "child_autonomy_required gate=autonomy" in output[1]


def test_status_explains_qualification_as_only_remaining_blocker() -> None:
    decision = MergeReadinessPolicy(now=lambda: NOW).evaluate(ready_facts())

    output = render_merge_eligibility(
        MergeEligibilityStatus("PR #42", decision, qualification_valid=False)
    )

    assert output[0] == (
        "MERGE-ELIGIBLE child PR #42: no; "
        "waiting=qualification; only_remaining=qualification"
    )
    assert output[1].startswith("  BLOCKER qualification_required:")
