from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from local_issue_runner.db import CoordinatorStore, QualificationStore
from local_issue_runner.models import (
    ParentDeliveryRecord,
    QualificationContext,
    QualificationLevel,
    QualificationReceipt,
)
from local_issue_runner.reconcile import (
    FeatureFinalizationRequest,
    finalize_feature,
    reconcile_scope,
)
from local_issue_runner.scope import (
    ChildRequirement,
    DeliveryState,
    FeatureScopeState,
    IntegrationState,
)


def test_reopened_child_reopens_delivered_parent_without_rewriting_history() -> None:
    requirement = ChildRequirement(101, "Child", "Implement child.")
    historical = DeliveryState(
        issue_number=101,
        delivery=1,
        scope=requirement,
        branch="runner/p-100/i-101/d-1",
        worktree=Path("runtime/worktrees/p-100-i-101-d-1"),
        pr_number=51,
        merged=True,
        issue_state="CLOSED",
    )
    previous = FeatureScopeState(
        repository="owner/project",
        parent_issue=100,
        feature_branch="feature/100",
        requirements=(requirement,),
        deliveries=(historical,),
        integration=IntegrationState(
            delivery=2,
            selected_children=(101,),
            ready=True,
            delivered=True,
        ),
        parent_issue_state="CLOSED",
    )

    plan = reconcile_scope(
        previous, requirements=(requirement,), issue_states={101: "OPEN"}
    )

    assert plan.reopen_parent is True
    assert plan.parent_delivery == 3
    assert plan.new_deliveries[0].delivery == 2
    assert previous.integration.delivery == 2
    assert previous.deliveries == (historical,)


def test_finalization_retains_feature_branch_logs_database_and_qualification(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runner.db"
    store = CoordinatorStore(database)
    qualification_store = QualificationStore(database)
    parent = ParentDeliveryRecord("owner/project", 100, 1, (101,))
    store.record_parent_delivery(parent)
    receipt = QualificationReceipt(
        level=QualificationLevel.FULL,
        disposable_repository="owner/disposable",
        exercise_id="full-7",
        successful=True,
        observed_transitions=(
            "dependency",
            "parallel_work",
            "feedback",
            "restart_recovery",
            "parent_integration",
        ),
        context=QualificationContext(
            "runtime-v1", (("gh", "2.80.0"),), "policy-v1"
        ),
        completed_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
        evidence_paths=("evidence/full.json",),
    )
    qualification_store.record(receipt)
    feature_branch = tmp_path / "refs" / "heads" / "feature-100"
    log = tmp_path / "logs" / "parent-100.log"
    evidence = tmp_path / "evidence" / "full.json"
    for path, contents in (
        (feature_branch, "feature tip"),
        (log, "retained job log"),
        (evidence, "qualification evidence"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    finalize_feature(
        FeatureFinalizationRequest(
            repository="owner/project",
            parent_issue=100,
            delivery=1,
            feature_branch=feature_branch,
            retained_paths=(log, evidence),
        ),
        store=store,
    )

    assert feature_branch.read_text(encoding="utf-8") == "feature tip"
    assert log.read_text(encoding="utf-8") == "retained job log"
    assert evidence.read_text(encoding="utf-8") == "qualification evidence"
    assert CoordinatorStore(database).parent_deliveries("owner/project", 100) == (
        parent,
    )
    assert QualificationStore(database).latest_successful(
        QualificationLevel.FULL
    ) == receipt
