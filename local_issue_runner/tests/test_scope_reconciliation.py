from __future__ import annotations

from pathlib import Path

import pytest

from local_issue_runner.reconcile import ScopeActionKind, reconcile_scope
from local_issue_runner.scope import (
    ChildRequirement,
    DeliveryState,
    FeatureScopeState,
    IntegrationState,
    LinkedSpecRevision,
)


def requirement(
    issue_number: int,
    *,
    body: str | None = None,
    spec_revision: str = "spec-v1",
) -> ChildRequirement:
    return ChildRequirement(
        issue_number=issue_number,
        title=f"Issue {issue_number}",
        body=body or f"Implement issue {issue_number}.",
        linked_specs=(
            LinkedSpecRevision(path="docs/spec.md", revision=spec_revision),
        ),
    )


def delivery(
    issue_number: int,
    *,
    number: int = 1,
    merged: bool = False,
    issue_state: str = "OPEN",
    prerequisites: tuple[int, ...] = (),
) -> DeliveryState:
    slug = f"p-100-i-{issue_number}-d-{number}"
    return DeliveryState(
        issue_number=issue_number,
        delivery=number,
        scope=requirement(issue_number),
        branch=f"runner/p-100/i-{issue_number}/d-{number}",
        worktree=Path("runtime/worktrees") / slug,
        pr_number=issue_number + 1000,
        merged=merged,
        issue_state=issue_state,
        prerequisites=prerequisites,
        job_running=not merged,
    )


def feature(
    *,
    requirements: tuple[ChildRequirement, ...],
    deliveries: tuple[DeliveryState, ...],
    integration_ready: bool = False,
    parent_delivered: bool = False,
    parent_issue_state: str = "OPEN",
    parent_delivery: int = 1,
) -> FeatureScopeState:
    return FeatureScopeState(
        repository="owner/project",
        parent_issue=100,
        feature_branch="feature/100",
        requirements=requirements,
        deliveries=deliveries,
        integration=IntegrationState(
            delivery=parent_delivery,
            selected_children=tuple(item.issue_number for item in requirements),
            ready=integration_ready,
            delivered=parent_delivered,
        ),
        parent_issue_state=parent_issue_state,
    )


@pytest.mark.parametrize(
    "changed",
    [
        requirement(101, body="The issue body now requires different behavior."),
        requirement(101, spec_revision="spec-v2"),
    ],
)
def test_changed_requirement_invalidates_only_affected_running_job(
    changed: ChildRequirement,
) -> None:
    previous = feature(
        requirements=(requirement(101), requirement(102)),
        deliveries=(delivery(101), delivery(102)),
    )

    plan = reconcile_scope(
        previous,
        requirements=(changed, requirement(102)),
        issue_states={101: "OPEN", 102: "OPEN"},
    )

    assert plan.cancelled_delivery_keys == ("owner/project:100:101:1",)
    assert plan.restart_issue_numbers == (101,)
    assert "owner/project:100:102:1" not in plan.cancelled_delivery_keys


def test_unrelated_sibling_addition_does_not_cancel_unaffected_deliveries() -> None:
    previous = feature(
        requirements=(requirement(101),),
        deliveries=(delivery(101),),
    )

    plan = reconcile_scope(
        previous,
        requirements=(requirement(101), requirement(102)),
        issue_states={101: "OPEN", 102: "OPEN"},
    )

    assert plan.cancelled_delivery_keys == ()
    assert plan.restart_issue_numbers == ()
    assert plan.new_delivery_issue_numbers == (102,)


def test_reopened_delivered_child_gets_fresh_delivery_workspace_branch_and_pr() -> None:
    historical = delivery(
        101,
        number=1,
        merged=True,
        issue_state="CLOSED",
    )
    previous = feature(
        requirements=(requirement(101),),
        deliveries=(historical,),
    )

    plan = reconcile_scope(
        previous,
        requirements=(requirement(101),),
        issue_states={101: "OPEN"},
    )

    fresh = plan.new_deliveries[0]
    assert fresh.issue_number == 101
    assert fresh.delivery == 2
    assert fresh.branch == "runner/p-100/i-101/d-2"
    assert fresh.worktree == Path("runtime/worktrees/p-100-i-101-d-2")
    assert fresh.pr_number is None
    assert fresh.scope == requirement(101)
    assert plan.actions_for(101) == (ScopeActionKind.START_FRESH_DELIVERY,)


def test_invalidated_prerequisite_pauses_only_unmerged_dependents() -> None:
    previous = feature(
        requirements=(requirement(101), requirement(102), requirement(103)),
        deliveries=(
            delivery(101, merged=True, issue_state="CLOSED"),
            delivery(102, prerequisites=(101,)),
            delivery(103, merged=True, issue_state="CLOSED", prerequisites=(101,)),
        ),
    )

    plan = reconcile_scope(
        previous,
        requirements=(requirement(101), requirement(102), requirement(103)),
        issue_states={101: "OPEN", 102: "OPEN", 103: "CLOSED"},
    )

    assert plan.paused_delivery_keys == ("owner/project:100:102:1",)
    assert "owner/project:100:103:1" not in plan.paused_delivery_keys


def test_reopened_child_reopens_delivered_parent_with_new_parent_delivery() -> None:
    previous = feature(
        requirements=(requirement(101),),
        deliveries=(delivery(101, merged=True, issue_state="CLOSED"),),
        integration_ready=True,
        parent_delivered=True,
        parent_issue_state="CLOSED",
        parent_delivery=3,
    )

    plan = reconcile_scope(
        previous,
        requirements=(requirement(101),),
        issue_states={101: "OPEN"},
    )

    assert plan.reopen_parent is True
    assert plan.parent_delivery == 4
    assert plan.actions_for_parent() == (
        ScopeActionKind.REOPEN_PARENT,
        ScopeActionKind.ALLOCATE_PARENT_DELIVERY,
    )


@pytest.mark.parametrize("excluded", [False, True])
def test_removed_or_excluded_child_invalidates_integration_readiness(
    excluded: bool,
) -> None:
    previous = feature(
        requirements=(requirement(101), requirement(102)),
        deliveries=(
            delivery(101, merged=True, issue_state="CLOSED"),
            delivery(102, merged=True, issue_state="CLOSED"),
        ),
        integration_ready=True,
    )

    plan = reconcile_scope(
        previous,
        requirements=(requirement(101),),
        issue_states={101: "CLOSED", 102: "CLOSED"},
        excluded_issue_numbers=(102,) if excluded else (),
    )

    assert plan.integration_ready is False
    assert plan.integration_blocker is not None
    assert plan.integration_blocker.issue_numbers == (102,)
    assert "remain or be reverted" in plan.integration_blocker.detail
    assert plan.actions_for(102) == (ScopeActionKind.REQUIRE_SCOPE_DECISION,)
