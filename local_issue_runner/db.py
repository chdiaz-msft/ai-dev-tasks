from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from local_issue_runner.completion import (
    AnyCompletionEvidence,
    CompletionDelivery,
    CompletionEvidence,
    CompletionMode,
    LocalCommitCompletionEvidence,
)
from local_issue_runner.github_api import ChildPullRequest
from local_issue_runner.migrations import migrate
from local_issue_runner.models import (
    ActionIntent,
    ActionOutcome,
    ActionState,
    BlockerCode,
    ChildPRIdentity,
    ChildPRRequest,
    ControlKind,
    ControlRequest,
    ControlScope,
    DependencyEdge,
    DependencyInvalidation,
    DiscoveryMode,
    FeatureSnapshot,
    FeedbackDisposition,
    FeedbackItem,
    IntegrationRepairChildRecord,
    IssueDeliveryRecord,
    OperationalBlocker,
    ParentDeliveryRecord,
    PauseState,
    ProblemCategory,
    ProblemRecord,
    ProcessIdentity,
    ProcessRecord,
    QualificationContext,
    QualificationLevel,
    QualificationReceipt,
    SnapshotBlocker,
    SnapshotTask,
    TaskId,
    TaskKind,
    ThreadState,
)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = FULL")
    migrate(connection)
    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        connection.close()
        raise RuntimeError("SQLite foreign-key enforcement could not be enabled")
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


def _encode_task_id(task_id: TaskId) -> str:
    return json.dumps(task_id, separators=(",", ":"))


def _decode_task_id(value: str) -> TaskId:
    decoded = json.loads(value)
    if not isinstance(decoded, (int, str)) or isinstance(decoded, bool):
        raise TypeError(f"invalid persisted task ID: {value!r}")
    return decoded


class QualificationStore:
    """Append-only live-exercise receipts used to gate automatic merging."""

    def __init__(self, path: Path) -> None:
        self.path = path
        connection = connect(path)
        connection.close()

    def record(self, receipt: QualificationReceipt) -> None:
        values = (
            receipt.level.value,
            receipt.disposable_repository,
            receipt.exercise_id,
            int(receipt.successful),
            json.dumps(receipt.observed_transitions, separators=(",", ":")),
            receipt.context.runtime_identity,
            json.dumps(receipt.context.tool_versions, separators=(",", ":")),
            receipt.context.policy_fingerprint,
            receipt.completed_at.isoformat(),
            json.dumps(receipt.evidence_paths, separators=(",", ":")),
        )
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO qualification_receipts(
                        level, disposable_repository, exercise_id, successful,
                        observed_transitions, runtime_identity, tool_versions,
                        policy_fingerprint, completed_at, evidence_paths
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(disposable_repository, exercise_id) DO NOTHING
                    """,
                    values,
                )
                row = connection.execute(
                    """
                    SELECT level, disposable_repository, exercise_id, successful,
                           observed_transitions, runtime_identity, tool_versions,
                           policy_fingerprint, completed_at, evidence_paths
                    FROM qualification_receipts
                    WHERE disposable_repository = ? AND exercise_id = ?
                    """,
                    (receipt.disposable_repository, receipt.exercise_id),
                ).fetchone()
                if row is None or self._decode(row) != receipt:
                    raise RuntimeError(
                        "qualification exercise already has different evidence"
                    )
        finally:
            connection.close()

    def latest_successful(
        self, level: QualificationLevel
    ) -> QualificationReceipt | None:
        connection = connect(self.path)
        try:
            row = connection.execute(
                """
                SELECT level, disposable_repository, exercise_id, successful,
                       observed_transitions, runtime_identity, tool_versions,
                       policy_fingerprint, completed_at, evidence_paths
                FROM qualification_receipts
                WHERE successful = 1
                  AND (level = ? OR level = 'full')
                ORDER BY completed_at DESC, id DESC
                LIMIT 1
                """,
                (level.value,),
            ).fetchone()
            return None if row is None else self._decode(row)
        finally:
            connection.close()

    @staticmethod
    def _decode(row: sqlite3.Row) -> QualificationReceipt:
        from datetime import datetime

        transitions = json.loads(row["observed_transitions"])
        tools = json.loads(row["tool_versions"])
        evidence = json.loads(row["evidence_paths"])
        if not isinstance(transitions, list) or not isinstance(tools, list):
            raise TypeError("invalid persisted qualification evidence")
        if not isinstance(evidence, list):
            raise TypeError("invalid persisted qualification evidence paths")
        return QualificationReceipt(
            level=QualificationLevel(row["level"]),
            disposable_repository=row["disposable_repository"],
            exercise_id=row["exercise_id"],
            successful=bool(row["successful"]),
            observed_transitions=tuple(str(item) for item in transitions),
            context=QualificationContext(
                runtime_identity=row["runtime_identity"],
                tool_versions=tuple((str(item[0]), str(item[1])) for item in tools),
                policy_fingerprint=row["policy_fingerprint"],
            ),
            completed_at=datetime.fromisoformat(row["completed_at"]),
            evidence_paths=tuple(str(item) for item in evidence),
        )


class FeedbackStore:
    """Durable feedback revisions and recoverable reply/resolution intents."""

    def __init__(self, path: Path) -> None:
        self.path = path
        connection = connect(path)
        connection.close()

    def is_runner_output(self, identity: str) -> bool:
        connection = connect(self.path)
        try:
            return (
                connection.execute(
                    "SELECT 1 FROM runner_feedback_outputs WHERE feedback_identity = ?",
                    (identity,),
                ).fetchone()
                is not None
            )
        finally:
            connection.close()

    def record_runner_output(self, identity: str) -> None:
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    "INSERT OR IGNORE INTO runner_feedback_outputs(feedback_identity) "
                    "VALUES (?)",
                    (identity,),
                )
        finally:
            connection.close()

    def latest_thread_state(self, thread_id: str) -> ThreadState | None:
        connection = connect(self.path)
        try:
            row = connection.execute(
                """
                SELECT latest_thread_state FROM feedback_items
                WHERE thread_id = ? AND latest_thread_state IS NOT NULL
                ORDER BY rowid DESC LIMIT 1
                """,
                (thread_id,),
            ).fetchone()
            return None if row is None else ThreadState(row[0])
        finally:
            connection.close()

    def begin_revision(
        self,
        item: FeedbackItem,
        revision: str,
        body_hash: str,
    ) -> bool:
        connection = connect(self.path)
        try:
            with transaction(connection):
                existing = connection.execute(
                    "SELECT handled_at FROM feedback_revisions "
                    "WHERE revision_fingerprint = ?",
                    (revision,),
                ).fetchone()
                if existing is not None:
                    return existing[0] is None
                connection.execute(
                    """
                    INSERT INTO feedback_items(
                        feedback_identity, kind, item_id, thread_id,
                        latest_revision, latest_thread_state, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(feedback_identity) DO UPDATE SET
                        thread_id = excluded.thread_id,
                        latest_revision = excluded.latest_revision,
                        latest_thread_state = excluded.latest_thread_state,
                        updated_at = excluded.updated_at
                    """,
                    (
                        item.identity,
                        item.kind.value,
                        item.item_id,
                        item.thread_id,
                        revision,
                        None if item.thread_state is None else item.thread_state.value,
                        item.updated_at,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO feedback_revisions(
                        revision_fingerprint, feedback_identity, author_login,
                        body_hash, source_updated_at, thread_state
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        revision,
                        item.identity,
                        item.author_login,
                        body_hash,
                        item.updated_at,
                        None if item.thread_state is None else item.thread_state.value,
                    ),
                )
                return True
        finally:
            connection.close()

    def record_reply_intent(
        self, revision: str, pr_number: int, reply_body: str
    ) -> str:
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO feedback_reply_intents(
                        revision_fingerprint, pr_number, reply_body
                    ) VALUES (?, ?, ?)
                    """,
                    (revision, pr_number, reply_body),
                )
                row = connection.execute(
                    "SELECT reply_body FROM feedback_reply_intents "
                    "WHERE revision_fingerprint = ?",
                    (revision,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("feedback reply intent was not persisted")
                return str(row[0])
        finally:
            connection.close()

    def complete_reply(self, revision: str, output_identity: str) -> None:
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    "UPDATE feedback_reply_intents SET output_identity = ? "
                    "WHERE revision_fingerprint = ?",
                    (output_identity, revision),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO runner_feedback_outputs(feedback_identity) "
                    "VALUES (?)",
                    (output_identity,),
                )
        finally:
            connection.close()

    def record_resolution_intent(self, revision: str, thread_id: str) -> None:
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO feedback_resolution_intents(
                        revision_fingerprint, thread_id
                    ) VALUES (?, ?)
                    """,
                    (revision, thread_id),
                )
        finally:
            connection.close()

    def complete_resolution(self, revision: str) -> None:
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    "UPDATE feedback_resolution_intents "
                    "SET completed_at = CURRENT_TIMESTAMP "
                    "WHERE revision_fingerprint = ?",
                    (revision,),
                )
        finally:
            connection.close()

    def complete_revision(
        self,
        revision: str,
        disposition: FeedbackDisposition,
        reason: str,
    ) -> None:
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    """
                    UPDATE feedback_revisions
                    SET disposition = ?, reason = ?, handled_at = CURRENT_TIMESTAMP
                    WHERE revision_fingerprint = ?
                    """,
                    (disposition.value, reason, revision),
                )
        finally:
            connection.close()

    def disposition(self, identity: str) -> FeedbackDisposition | None:
        connection = connect(self.path)
        try:
            row = connection.execute(
                """
                SELECT fr.disposition
                FROM feedback_items fi
                JOIN feedback_revisions fr
                  ON fr.revision_fingerprint = fi.latest_revision
                WHERE fi.feedback_identity = ?
                """,
                (identity,),
            ).fetchone()
            return (
                None if row is None or row[0] is None else FeedbackDisposition(row[0])
            )
        finally:
            connection.close()

    def restart_quiet_period(self, pr_number: int, revision: str) -> None:
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO pr_quiet_periods(pr_number, source_revision)
                    VALUES (?, ?)
                    ON CONFLICT(pr_number) DO UPDATE SET
                        restarted_at = CURRENT_TIMESTAMP,
                        source_revision = excluded.source_revision
                    """,
                    (pr_number, revision),
                )
        finally:
            connection.close()


class SnapshotStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def replace_all(self, snapshots: Iterable[FeatureSnapshot]) -> None:
        materialized = tuple(snapshots)
        feature_keys = [
            (snapshot.repository, snapshot.parent_issue) for snapshot in materialized
        ]
        if len(feature_keys) != len(set(feature_keys)):
            raise ValueError("only one snapshot per repository feature may be current")
        if any(not snapshot.complete for snapshot in materialized):
            raise ValueError(
                "incomplete graph snapshots cannot replace complete snapshots"
            )

        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute("UPDATE graph_snapshots SET is_current = 0")
                for snapshot in materialized:
                    self._insert_snapshot(connection, snapshot)
        finally:
            connection.close()

    def _insert_snapshot(
        self, connection: sqlite3.Connection, snapshot: FeatureSnapshot
    ) -> None:
        connection.execute(
            """
            INSERT INTO repositories(canonical_identity)
            VALUES (?)
            ON CONFLICT(canonical_identity) DO NOTHING
            """,
            (snapshot.repository,),
        )
        repository_id = connection.execute(
            "SELECT id FROM repositories WHERE canonical_identity = ?",
            (snapshot.repository,),
        ).fetchone()[0]
        connection.execute(
            """
            INSERT INTO features(
                repository_id, parent_issue, feature_branch, discovery_mode
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(repository_id, parent_issue) DO UPDATE SET
                feature_branch = excluded.feature_branch,
                discovery_mode = excluded.discovery_mode
            """,
            (
                repository_id,
                snapshot.parent_issue,
                snapshot.feature_branch,
                snapshot.discovery_mode.value,
            ),
        )
        feature_id = connection.execute(
            "SELECT id FROM features WHERE repository_id = ? AND parent_issue = ?",
            (repository_id, snapshot.parent_issue),
        ).fetchone()[0]
        cursor = connection.execute(
            """
            INSERT INTO graph_snapshots(
                feature_id, source_revision, is_complete, is_current
            ) VALUES (?, ?, 1, 1)
            """,
            (feature_id, snapshot.source_revision),
        )
        snapshot_id = cursor.lastrowid
        ready = set(snapshot.ready_issue_numbers)
        blocked = set(snapshot.blocked_issue_numbers)
        for order, issue_number in enumerate(snapshot.child_issues):
            connection.execute(
                """
                INSERT INTO snapshot_memberships(
                    snapshot_id, feature_id, membership_order, issue_number
                ) VALUES (?, ?, ?, ?)
                """,
                (snapshot_id, feature_id, order, issue_number),
            )
        for order, task in enumerate(snapshot.tasks):
            connection.execute(
                """
                INSERT INTO snapshot_tasks(
                    snapshot_id, feature_id, task_id, task_order, kind,
                    issue_number, title, source_revision, is_ready, is_blocked
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    feature_id,
                    _encode_task_id(task.task_id),
                    order,
                    task.kind.value,
                    task.issue_number,
                    task.title,
                    task.source_revision,
                    int(task.issue_number in ready),
                    int(task.issue_number in blocked),
                ),
            )
        for edge in snapshot.dependency_edges:
            connection.execute(
                """
                INSERT INTO dependency_edges(
                    snapshot_id, feature_id, prerequisite_task_id,
                    dependent_task_id, source_relationship_id, source_revision
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    feature_id,
                    _encode_task_id(edge.prerequisite_task_id),
                    _encode_task_id(edge.dependent_task_id),
                    edge.source_relationship_id,
                    edge.source_revision,
                ),
            )
        for order, blocker in enumerate(snapshot.blockers):
            connection.execute(
                """
                INSERT INTO snapshot_blockers(
                    snapshot_id, feature_id, blocker_order, code, detail,
                    issue_numbers
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    feature_id,
                    order,
                    blocker.code.value,
                    blocker.detail,
                    json.dumps(blocker.issue_numbers),
                ),
            )

    def load_all(self) -> tuple[FeatureSnapshot, ...]:
        connection = connect(self.path)
        try:
            rows = connection.execute(
                """
                SELECT gs.id AS snapshot_id, gs.feature_id, gs.source_revision,
                       gs.is_complete, r.canonical_identity, f.parent_issue,
                       f.feature_branch, f.discovery_mode
                FROM graph_snapshots gs
                JOIN features f ON f.id = gs.feature_id
                JOIN repositories r ON r.id = f.repository_id
                WHERE gs.is_current = 1
                ORDER BY r.canonical_identity, f.parent_issue
                """
            ).fetchall()
            return tuple(self._load_snapshot(connection, row) for row in rows)
        finally:
            connection.close()

    def _load_snapshot(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> FeatureSnapshot:
        snapshot_id = row["snapshot_id"]
        feature_id = row["feature_id"]
        task_rows = connection.execute(
            """
            SELECT * FROM snapshot_tasks
            WHERE snapshot_id = ? AND feature_id = ?
            ORDER BY task_order
            """,
            (snapshot_id, feature_id),
        ).fetchall()
        tasks = tuple(
            SnapshotTask(
                task_id=_decode_task_id(task["task_id"]),
                kind=TaskKind(task["kind"]),
                issue_number=task["issue_number"],
                title=task["title"],
                source_revision=task["source_revision"],
            )
            for task in task_rows
        )
        edge_rows = connection.execute(
            """
            SELECT * FROM dependency_edges
            WHERE snapshot_id = ? AND feature_id = ?
            ORDER BY prerequisite_task_id, dependent_task_id
            """,
            (snapshot_id, feature_id),
        ).fetchall()
        edges = tuple(
            DependencyEdge(
                prerequisite_task_id=_decode_task_id(edge["prerequisite_task_id"]),
                dependent_task_id=_decode_task_id(edge["dependent_task_id"]),
                source_relationship_id=edge["source_relationship_id"],
                source_revision=edge["source_revision"],
            )
            for edge in edge_rows
        )
        blocker_rows = connection.execute(
            """
            SELECT * FROM snapshot_blockers
            WHERE snapshot_id = ? AND feature_id = ?
            ORDER BY blocker_order
            """,
            (snapshot_id, feature_id),
        ).fetchall()
        blockers = tuple(
            SnapshotBlocker(
                code=BlockerCode(blocker["code"]),
                detail=blocker["detail"],
                issue_numbers=tuple(json.loads(blocker["issue_numbers"])),
            )
            for blocker in blocker_rows
        )
        child_issues = tuple(
            membership["issue_number"]
            for membership in connection.execute(
                """
                SELECT issue_number FROM snapshot_memberships
                WHERE snapshot_id = ? AND feature_id = ?
                ORDER BY membership_order
                """,
                (snapshot_id, feature_id),
            ).fetchall()
        )
        ready = tuple(
            task["issue_number"]
            for task in task_rows
            if task["is_ready"] and task["issue_number"] is not None
        )
        blocked = tuple(
            task["issue_number"]
            for task in task_rows
            if task["is_blocked"] and task["issue_number"] is not None
        )
        return FeatureSnapshot(
            repository=row["canonical_identity"],
            parent_issue=row["parent_issue"],
            feature_branch=row["feature_branch"],
            discovery_mode=DiscoveryMode(row["discovery_mode"]),
            child_issues=child_issues,
            tasks=tasks,
            dependency_edges=edges,
            complete=bool(row["is_complete"]),
            source_revision=row["source_revision"],
            blockers=blockers,
            ready_issue_numbers=ready,
            blocked_issue_numbers=blocked,
        )


def _control_request(row: sqlite3.Row) -> ControlRequest:
    return ControlRequest(
        request_id=row["id"],
        kind=ControlKind(row["kind"]),
        scope=ControlScope(
            parent_issue=row["parent_issue"],
            issue_number=row["issue_number"],
        ),
        created_at=row["created_at"],
    )


def _action_intent(row: sqlite3.Row) -> ActionIntent:
    return ActionIntent(
        key=row["intent_key"],
        kind=row["kind"],
        target=row["target"],
        expected_input=row["expected_input"],
    )


class CoordinatorStore:
    """Short-transaction persistence for controls and recoverable mutations."""

    def __init__(self, path: Path) -> None:
        self.path = path
        connection = connect(path)
        connection.close()

    def record_repair_child(self, record: IntegrationRepairChildRecord) -> None:
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO integration_repair_children(
                        repository, parent_issue, finding_identity,
                        issue_number, source
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(repository, parent_issue, finding_identity)
                    DO NOTHING
                    """,
                    (
                        record.repository,
                        record.parent_issue,
                        record.finding_identity,
                        record.issue_number,
                        record.source,
                    ),
                )
                existing = connection.execute(
                    """
                    SELECT issue_number, source
                    FROM integration_repair_children
                    WHERE repository = ? AND parent_issue = ?
                      AND finding_identity = ?
                    """,
                    (
                        record.repository,
                        record.parent_issue,
                        record.finding_identity,
                    ),
                ).fetchone()
                if existing is None or (
                    existing["issue_number"],
                    existing["source"],
                ) != (record.issue_number, record.source):
                    raise RuntimeError(
                        "repair child finding already has different origin evidence"
                    )
        finally:
            connection.close()

    def repair_child_for_finding(
        self, repository: str, parent_issue: int, finding_identity: str
    ) -> IntegrationRepairChildRecord | None:
        connection = connect(self.path)
        try:
            row = connection.execute(
                """
                SELECT repository, parent_issue, finding_identity,
                       issue_number, source
                FROM integration_repair_children
                WHERE repository = ? AND parent_issue = ?
                  AND finding_identity = ?
                """,
                (repository, parent_issue, finding_identity),
            ).fetchone()
            return (
                None
                if row is None
                else IntegrationRepairChildRecord(
                    row["repository"],
                    row["parent_issue"],
                    row["finding_identity"],
                    row["issue_number"],
                    row["source"],
                )
            )
        finally:
            connection.close()

    def repair_children(
        self, repository: str, parent_issue: int
    ) -> tuple[IntegrationRepairChildRecord, ...]:
        connection = connect(self.path)
        try:
            rows = connection.execute(
                """
                SELECT repository, parent_issue, finding_identity,
                       issue_number, source
                FROM integration_repair_children
                WHERE repository = ? AND parent_issue = ?
                ORDER BY created_at, issue_number
                """,
                (repository, parent_issue),
            ).fetchall()
            return tuple(
                IntegrationRepairChildRecord(
                    row["repository"],
                    row["parent_issue"],
                    row["finding_identity"],
                    row["issue_number"],
                    row["source"],
                )
                for row in rows
            )
        finally:
            connection.close()

    @staticmethod
    def _problem(row: sqlite3.Row) -> ProblemRecord:
        return ProblemRecord(
            identity=str(row["identity"]),
            category=ProblemCategory(row["category"]),
            detail=str(row["detail"]),
            streak=int(row["streak"]),
            stopped=bool(row["stopped"]),
            retry_reason=row["retry_reason"],
        )

    def problem(self, identity: str) -> ProblemRecord | None:
        connection = connect(self.path)
        try:
            row = connection.execute(
                "SELECT * FROM unresolved_problems WHERE identity = ?", (identity,)
            ).fetchone()
            return None if row is None else self._problem(row)
        finally:
            connection.close()

    def record_no_progress(
        self,
        identity: str,
        category: ProblemCategory,
        detail: str,
    ) -> ProblemRecord:
        if not identity.strip() or not detail.strip():
            raise ValueError("problem identity and detail are required")
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO unresolved_problems(identity, category, detail, streak)
                    VALUES (?, ?, ?, 1)
                    ON CONFLICT(identity) DO UPDATE SET
                        category = excluded.category,
                        detail = excluded.detail,
                        streak = unresolved_problems.streak + 1,
                        stopped = CASE
                            WHEN unresolved_problems.streak + 1 >= 2 THEN 1 ELSE 0
                        END,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (identity, category.value, detail),
                )
                row = connection.execute(
                    "SELECT * FROM unresolved_problems WHERE identity = ?", (identity,)
                ).fetchone()
            return self._problem(row)
        finally:
            connection.close()

    def retry_problem(self, identity: str, reason: str) -> ProblemRecord:
        if not identity.strip() or not reason.strip():
            raise ValueError("problem identity and retry reason are required")
        connection = connect(self.path)
        try:
            with transaction(connection):
                cursor = connection.execute(
                    """
                    UPDATE unresolved_problems
                    SET streak = 0, stopped = 0, retry_reason = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE identity = ?
                    """,
                    (reason.strip(), identity),
                )
                if cursor.rowcount != 1:
                    raise ValueError(f"unknown problem identity: {identity}")
                connection.execute(
                    "DELETE FROM operational_blockers WHERE identity = ?", (identity,)
                )
                row = connection.execute(
                    "SELECT * FROM unresolved_problems WHERE identity = ?", (identity,)
                ).fetchone()
            return self._problem(row)
        finally:
            connection.close()

    def record_blocker(
        self, identity: str, category: ProblemCategory, detail: str
    ) -> None:
        blocker = OperationalBlocker(identity, category, detail)
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO operational_blockers(identity, category, detail)
                    VALUES (?, ?, ?)
                    ON CONFLICT(identity) DO UPDATE SET
                        category = excluded.category,
                        detail = excluded.detail,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (blocker.identity, blocker.category.value, blocker.detail),
                )
        finally:
            connection.close()

    def blockers(self) -> tuple[OperationalBlocker, ...]:
        connection = connect(self.path)
        try:
            rows = connection.execute(
                "SELECT * FROM operational_blockers ORDER BY identity"
            ).fetchall()
            return tuple(
                OperationalBlocker(
                    str(row["identity"]),
                    ProblemCategory(row["category"]),
                    str(row["detail"]),
                )
                for row in rows
            )
        finally:
            connection.close()

    @staticmethod
    def _require_same_record(
        persisted: sqlite3.Row | None,
        expected: tuple[object, ...],
        columns: tuple[str, ...],
        kind: str,
    ) -> None:
        if (
            persisted is None
            or tuple(persisted[column] for column in columns) != expected
        ):
            raise ValueError(f"{kind} identity already has different historical data")

    def last_served_parent(self, repository: str) -> int | None:
        connection = connect(self.path)
        try:
            row = connection.execute(
                "SELECT last_served_parent FROM scheduling_positions "
                "WHERE repository = ?",
                (repository,),
            ).fetchone()
            return None if row is None else int(row["last_served_parent"])
        finally:
            connection.close()

    def record_last_served_parent(self, repository: str, parent_issue: int) -> None:
        if not repository.strip():
            raise ValueError("repository is required")
        if parent_issue <= 0:
            raise ValueError("parent issue must be positive")
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO scheduling_positions(repository, last_served_parent)
                    VALUES (?, ?)
                    ON CONFLICT(repository) DO UPDATE SET
                        last_served_parent = excluded.last_served_parent,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (repository, parent_issue),
                )
        finally:
            connection.close()

    def record_issue_delivery(self, record: IssueDeliveryRecord) -> None:
        connection = connect(self.path)
        try:
            with transaction(connection):
                values = (
                    record.key,
                    record.repository,
                    record.parent_issue,
                    record.issue_number,
                    record.delivery,
                    record.scope_fingerprint,
                    record.branch,
                    str(record.worktree),
                )
                connection.execute(
                    """
                    INSERT INTO issue_deliveries(
                        delivery_key, repository, parent_issue, issue_number,
                        delivery, scope_fingerprint, branch, worktree
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(delivery_key) DO NOTHING
                    """,
                    values,
                )
                row = connection.execute(
                    "SELECT * FROM issue_deliveries WHERE delivery_key = ?",
                    (record.key,),
                ).fetchone()
                self._require_same_record(
                    row,
                    values[1:],
                    (
                        "repository",
                        "parent_issue",
                        "issue_number",
                        "delivery",
                        "scope_fingerprint",
                        "branch",
                        "worktree",
                    ),
                    "issue delivery",
                )
        finally:
            connection.close()

    def issue_deliveries(
        self, repository: str, parent_issue: int, issue_number: int
    ) -> tuple[IssueDeliveryRecord, ...]:
        connection = connect(self.path)
        try:
            rows = connection.execute(
                """
                SELECT * FROM issue_deliveries
                WHERE repository = ? AND parent_issue = ? AND issue_number = ?
                ORDER BY delivery
                """,
                (repository, parent_issue, issue_number),
            ).fetchall()
            return tuple(
                IssueDeliveryRecord(
                    row["repository"],
                    row["parent_issue"],
                    row["issue_number"],
                    row["delivery"],
                    row["scope_fingerprint"],
                    row["branch"],
                    Path(row["worktree"]),
                )
                for row in rows
            )
        finally:
            connection.close()

    def record_parent_delivery(self, record: ParentDeliveryRecord) -> None:
        encoded = json.dumps(record.selected_children, separators=(",", ":"))
        connection = connect(self.path)
        try:
            with transaction(connection):
                values = (
                    record.key,
                    record.repository,
                    record.parent_issue,
                    record.delivery,
                    encoded,
                )
                connection.execute(
                    """
                    INSERT INTO parent_deliveries(
                        delivery_key, repository, parent_issue, delivery,
                        selected_children
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(delivery_key) DO NOTHING
                    """,
                    values,
                )
                row = connection.execute(
                    "SELECT * FROM parent_deliveries WHERE delivery_key = ?",
                    (record.key,),
                ).fetchone()
                self._require_same_record(
                    row,
                    values[1:],
                    ("repository", "parent_issue", "delivery", "selected_children"),
                    "parent delivery",
                )
        finally:
            connection.close()

    def parent_deliveries(
        self, repository: str, parent_issue: int
    ) -> tuple[ParentDeliveryRecord, ...]:
        connection = connect(self.path)
        try:
            rows = connection.execute(
                """
                SELECT * FROM parent_deliveries
                WHERE repository = ? AND parent_issue = ?
                ORDER BY delivery
                """,
                (repository, parent_issue),
            ).fetchall()
            return tuple(
                ParentDeliveryRecord(
                    row["repository"],
                    row["parent_issue"],
                    row["delivery"],
                    tuple(json.loads(row["selected_children"])),
                )
                for row in rows
            )
        finally:
            connection.close()

    def record_dependency_invalidation(
        self, invalidation: DependencyInvalidation
    ) -> None:
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO dependency_invalidations(
                        repository, parent_issue, prerequisite_issue,
                        dependent_issue, prerequisite_delivery
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        invalidation.repository,
                        invalidation.parent_issue,
                        invalidation.prerequisite_issue,
                        invalidation.dependent_issue,
                        invalidation.prerequisite_delivery,
                    ),
                )
        finally:
            connection.close()

    def dependency_invalidations(
        self, repository: str, parent_issue: int
    ) -> tuple[DependencyInvalidation, ...]:
        connection = connect(self.path)
        try:
            rows = connection.execute(
                """
                SELECT * FROM dependency_invalidations
                WHERE repository = ? AND parent_issue = ?
                ORDER BY prerequisite_issue, dependent_issue, prerequisite_delivery
                """,
                (repository, parent_issue),
            ).fetchall()
            return tuple(
                DependencyInvalidation(
                    row["repository"],
                    row["parent_issue"],
                    row["prerequisite_issue"],
                    row["dependent_issue"],
                    row["prerequisite_delivery"],
                )
                for row in rows
            )
        finally:
            connection.close()

    def record_control(self, kind: ControlKind, scope: ControlScope) -> ControlRequest:
        connection = connect(self.path)
        try:
            with transaction(connection):
                cursor = connection.execute(
                    """
                    INSERT INTO control_requests(
                        kind, scope_key, parent_issue, issue_number
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        kind.value,
                        scope.key,
                        scope.parent_issue,
                        scope.issue_number,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM control_requests WHERE id = ?",
                    (cursor.lastrowid,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("new control request could not be read")
                return _control_request(row)
        finally:
            connection.close()

    def pending_controls(self) -> tuple[ControlRequest, ...]:
        connection = connect(self.path)
        try:
            rows = connection.execute(
                """
                SELECT * FROM control_requests
                WHERE applied_at IS NULL
                ORDER BY id
                """
            ).fetchall()
            return tuple(_control_request(row) for row in rows)
        finally:
            connection.close()

    def apply_pending_controls(self) -> tuple[ControlRequest, ...]:
        connection = connect(self.path)
        try:
            with transaction(connection):
                rows = connection.execute(
                    """
                    SELECT * FROM control_requests
                    WHERE applied_at IS NULL
                    ORDER BY id
                    """
                ).fetchall()
                requests = tuple(_control_request(row) for row in rows)
                for request in requests:
                    connection.execute(
                        """
                        INSERT INTO pause_states(
                            scope_key, parent_issue, issue_number, paused
                        ) VALUES (?, ?, ?, ?)
                        ON CONFLICT(scope_key) DO UPDATE SET
                            parent_issue = excluded.parent_issue,
                            issue_number = excluded.issue_number,
                            paused = excluded.paused,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            request.scope.key,
                            request.scope.parent_issue,
                            request.scope.issue_number,
                            int(request.kind is ControlKind.PAUSE),
                        ),
                    )
                if requests:
                    connection.executemany(
                        """
                        UPDATE control_requests
                        SET applied_at = CURRENT_TIMESTAMP
                        WHERE id = ? AND applied_at IS NULL
                        """,
                        ((request.request_id,) for request in requests),
                    )
                return requests
        finally:
            connection.close()

    def pause_state(self, scope: ControlScope) -> PauseState:
        connection = connect(self.path)
        try:
            row = connection.execute(
                "SELECT * FROM pause_states WHERE scope_key = ?",
                (scope.key,),
            ).fetchone()
            if row is None:
                return PauseState(scope=scope, paused=False)
            return PauseState(
                scope=scope,
                paused=bool(row["paused"]),
                updated_at=row["updated_at"],
            )
        finally:
            connection.close()

    def completion(
        self,
        delivery_key: str,
        mode: CompletionMode = CompletionMode.PULL_REQUEST,
    ) -> AnyCompletionEvidence | None:
        connection = connect(self.path)
        try:
            if mode is CompletionMode.LOCAL_COMMIT:
                row = connection.execute(
                    "SELECT * FROM local_completion_evidence WHERE delivery_key = ?",
                    (delivery_key,),
                ).fetchone()
                if row is None:
                    return None
                delivery = CompletionDelivery(
                    repository=row["repository"],
                    parent_issue=row["parent_issue"],
                    issue_number=row["issue_number"],
                    delivery=row["delivery"],
                    pr_number=None,
                    base_ref=row["feature_branch"],
                    base_commit=row["feature_base"],
                    work_head=None,
                    scope_fingerprint=row["scope_fingerprint"],
                    job_succeeded=True,
                )
                return LocalCommitCompletionEvidence(
                    delivery=delivery,
                    feature_branch=row["feature_branch"],
                    feature_base=row["feature_base"],
                    resulting_commit=row["resulting_commit"],
                    scope_fingerprint=row["scope_fingerprint"],
                    validation_identity=row["validation_identity"],
                )
            if mode is not CompletionMode.PULL_REQUEST:
                raise ValueError(
                    "acknowledgement evidence is accessed via acknowledgement()"
                )
            row = connection.execute(
                "SELECT * FROM completion_evidence WHERE delivery_key = ?",
                (delivery_key,),
            ).fetchone()
            if row is None:
                return None
            delivery = CompletionDelivery(
                repository=row["repository"],
                parent_issue=row["parent_issue"],
                issue_number=row["issue_number"],
                delivery=row["delivery"],
                pr_number=row["pr_number"],
                base_ref=row["base_ref"],
                base_commit=row["base_commit"],
                work_head=row["work_head"],
                scope_fingerprint=row["scope_fingerprint"],
                job_succeeded=True,
            )
            return CompletionEvidence(
                delivery=delivery,
                pr_number=row["pr_number"],
                resulting_commit=row["resulting_commit"],
                recorded_base=row["base_commit"],
                base_ref=row["base_ref"],
                work_head=row["work_head"],
                scope_fingerprint=row["scope_fingerprint"],
            )
        finally:
            connection.close()

    def record_completion(self, evidence: AnyCompletionEvidence) -> None:
        if isinstance(evidence, LocalCommitCompletionEvidence):
            self._record_local_completion(evidence)
            return
        delivery = evidence.delivery
        connection = connect(self.path)
        try:
            with transaction(connection):
                conflicting = connection.execute(
                    "SELECT 1 FROM local_completion_evidence WHERE delivery_key = ?",
                    (delivery.key,),
                ).fetchone()
                if conflicting is not None:
                    raise ValueError("delivery already has local completion evidence")
                connection.execute(
                    """
                    INSERT INTO completion_evidence(
                        delivery_key, repository, parent_issue, issue_number,
                        delivery, pr_number, base_ref, base_commit, work_head,
                        scope_fingerprint, resulting_commit
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(delivery_key) DO NOTHING
                    """,
                    (
                        delivery.key,
                        delivery.repository,
                        delivery.parent_issue,
                        delivery.issue_number,
                        delivery.delivery,
                        evidence.pr_number,
                        evidence.base_ref,
                        evidence.recorded_base,
                        evidence.work_head,
                        evidence.scope_fingerprint,
                        evidence.resulting_commit,
                    ),
                )
                persisted = connection.execute(
                    "SELECT resulting_commit, pr_number FROM completion_evidence "
                    "WHERE delivery_key = ?",
                    (delivery.key,),
                ).fetchone()
                if persisted is None or (
                    persisted["resulting_commit"],
                    persisted["pr_number"],
                ) != (evidence.resulting_commit, evidence.pr_number):
                    raise ValueError(
                        "delivery already has different completion evidence"
                    )
        finally:
            connection.close()

    def _record_local_completion(self, evidence: LocalCommitCompletionEvidence) -> None:
        delivery = evidence.delivery
        connection = connect(self.path)
        try:
            with transaction(connection):
                conflicting = connection.execute(
                    "SELECT 1 FROM completion_evidence WHERE delivery_key = ?",
                    (delivery.key,),
                ).fetchone()
                if conflicting is not None:
                    raise ValueError(
                        "delivery already has pull request completion evidence"
                    )
                connection.execute(
                    """
                    INSERT INTO local_completion_evidence(
                        delivery_key, repository, parent_issue, issue_number,
                        delivery, feature_branch, feature_base, resulting_commit,
                        scope_fingerprint, validation_identity
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(delivery_key) DO NOTHING
                    """,
                    (
                        delivery.key,
                        delivery.repository,
                        delivery.parent_issue,
                        delivery.issue_number,
                        delivery.delivery,
                        evidence.feature_branch,
                        evidence.feature_base,
                        evidence.resulting_commit,
                        evidence.scope_fingerprint,
                        evidence.validation_identity,
                    ),
                )
                persisted = connection.execute(
                    """
                    SELECT repository, parent_issue, issue_number, delivery,
                           feature_branch, feature_base, resulting_commit,
                           scope_fingerprint, validation_identity
                    FROM local_completion_evidence
                    WHERE delivery_key = ?
                    """,
                    (delivery.key,),
                ).fetchone()
                expected = (
                    delivery.repository,
                    delivery.parent_issue,
                    delivery.issue_number,
                    delivery.delivery,
                    evidence.feature_branch,
                    evidence.feature_base,
                    evidence.resulting_commit,
                    evidence.scope_fingerprint,
                    evidence.validation_identity,
                )
                if persisted is None or tuple(persisted) != expected:
                    raise ValueError(
                        "delivery already has different local completion evidence"
                    )
        finally:
            connection.close()

    def acknowledgement(self, delivery_key: str) -> str | None:
        connection = connect(self.path)
        try:
            row = connection.execute(
                "SELECT reason FROM completion_acknowledgements WHERE delivery_key = ?",
                (delivery_key,),
            ).fetchone()
            return None if row is None else str(row["reason"])
        finally:
            connection.close()

    def record_acknowledgement(self, delivery_key: str, reason: str) -> None:
        normalized = reason.strip()
        if not normalized:
            raise ValueError("an acknowledgement reason is required")
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO completion_acknowledgements(delivery_key, reason)
                    VALUES (?, ?)
                    ON CONFLICT(delivery_key) DO UPDATE SET
                        reason = excluded.reason,
                        recorded_at = CURRENT_TIMESTAMP
                    """,
                    (delivery_key, normalized),
                )
        finally:
            connection.close()

    def has_child_pr_for_delivery(
        self, repository: str, parent_issue: int, issue_number: int, delivery: int
    ) -> bool:
        connection = connect(self.path)
        try:
            row = connection.execute(
                """
                SELECT 1 FROM child_pr_creations
                WHERE repository = ? AND parent_issue = ?
                  AND issue_number = ? AND delivery = ?
                LIMIT 1
                """,
                (repository, parent_issue, issue_number, delivery),
            ).fetchone()
            return row is not None
        finally:
            connection.close()

    def record_action_intent(self, intent: ActionIntent) -> ActionState:
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO action_intents(
                        intent_key, kind, target, expected_input
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(intent_key) DO NOTHING
                    """,
                    (
                        intent.key,
                        intent.kind,
                        intent.target,
                        intent.expected_input,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM action_intents WHERE intent_key = ?",
                    (intent.key,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("action intent could not be read")
                if _action_intent(row) != intent:
                    raise ValueError(
                        "an action intent key cannot identify different inputs"
                    )
                return ActionState(row["state"])
        finally:
            connection.close()

    def unfinished_action_intents(self) -> tuple[ActionIntent, ...]:
        connection = connect(self.path)
        try:
            rows = connection.execute(
                """
                SELECT * FROM action_intents
                WHERE state IN ('pending', 'running')
                ORDER BY created_at, intent_key
                """
            ).fetchall()
            return tuple(_action_intent(row) for row in rows)
        finally:
            connection.close()

    def action_state(self, key: str) -> ActionState | None:
        connection = connect(self.path)
        try:
            row = connection.execute(
                "SELECT state FROM action_intents WHERE intent_key = ?",
                (key,),
            ).fetchone()
            return None if row is None else ActionState(row["state"])
        finally:
            connection.close()

    def mark_action_running(self, key: str) -> None:
        self._set_nonterminal_action_state(key, ActionState.RUNNING)

    def _set_nonterminal_action_state(self, key: str, state: ActionState) -> None:
        if state.terminal:
            raise ValueError("use record_action_outcome for terminal states")
        connection = connect(self.path)
        try:
            with transaction(connection):
                cursor = connection.execute(
                    """
                    UPDATE action_intents
                    SET state = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE intent_key = ? AND state IN ('pending', 'running')
                    """,
                    (state.value, key),
                )
                if cursor.rowcount != 1:
                    raise KeyError(f"unfinished action intent not found: {key}")
        finally:
            connection.close()

    def record_action_outcome(
        self,
        key: str,
        state: ActionState,
        detail: str | None = None,
    ) -> ActionOutcome:
        connection = connect(self.path)
        try:
            with transaction(connection):
                row = connection.execute(
                    "SELECT state FROM action_intents WHERE intent_key = ?",
                    (key,),
                ).fetchone()
                if row is None:
                    raise KeyError(f"action intent not found: {key}")
                existing = connection.execute(
                    "SELECT * FROM action_outcomes WHERE intent_key = ?",
                    (key,),
                ).fetchone()
                if existing is not None:
                    persisted = ActionOutcome(
                        intent_key=key,
                        state=ActionState(existing["state"]),
                        detail=existing["detail"],
                        observed_at=existing["observed_at"],
                    )
                    if persisted.state is not state or persisted.detail != detail:
                        raise ValueError(
                            "action intent already has a different outcome"
                        )
                    return persisted
                connection.execute(
                    """
                    INSERT INTO action_outcomes(intent_key, state, detail)
                    VALUES (?, ?, ?)
                    """,
                    (key, state.value, detail),
                )
                connection.execute(
                    """
                    UPDATE action_intents
                    SET state = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE intent_key = ?
                    """,
                    (state.value, key),
                )
                observed_at = connection.execute(
                    """
                    SELECT observed_at FROM action_outcomes
                    WHERE intent_key = ?
                    """,
                    (key,),
                ).fetchone()[0]
                return ActionOutcome(key, state, detail, observed_at)
        finally:
            connection.close()

    def action_outcome(self, key: str) -> ActionOutcome | None:
        connection = connect(self.path)
        try:
            row = connection.execute(
                "SELECT * FROM action_outcomes WHERE intent_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            return ActionOutcome(
                intent_key=key,
                state=ActionState(row["state"]),
                detail=row["detail"],
                observed_at=row["observed_at"],
            )
        finally:
            connection.close()

    def record_process(
        self, *, worktree: Path, identity: ProcessIdentity, state: ActionState
    ) -> None:
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO owned_processes(
                        worktree, pid, process_created_at, session_id, state
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(worktree) DO UPDATE SET
                        pid=excluded.pid, process_created_at=excluded.process_created_at,
                        session_id=excluded.session_id, state=excluded.state,
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        str(worktree.resolve()),
                        identity.pid,
                        identity.created_at,
                        identity.session_id,
                        state.value,
                    ),
                )
        finally:
            connection.close()

    def unresolved_processes(self) -> tuple[ProcessRecord, ...]:
        connection = connect(self.path)
        try:
            rows = connection.execute(
                """
                SELECT * FROM owned_processes
                WHERE state IN ('pending', 'running', 'ambiguous')
                ORDER BY worktree
                """
            ).fetchall()
            return tuple(
                ProcessRecord(
                    Path(row["worktree"]),
                    ProcessIdentity(
                        row["pid"], row["process_created_at"], row["session_id"]
                    ),
                    ActionState(row["state"]),
                )
                for row in rows
            )
        finally:
            connection.close()


class ChildPRStore(CoordinatorStore):
    def record_push_intent(
        self, request: ChildPRRequest, intent_key: str
    ) -> ActionState:
        return self.record_action_intent(
            ActionIntent(
                intent_key,
                "push_child_branch",
                request.identity.key,
                json.dumps(
                    {
                        "head_ref": request.head_ref,
                        "head_sha": request.head_sha,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )

    def record_pr_creation_intent(
        self, request: ChildPRRequest, intent_key: str
    ) -> ActionState:
        return self.record_action_intent(
            ActionIntent(
                intent_key,
                "create_child_pr",
                request.identity.key,
                json.dumps(
                    {
                        "head_ref": request.head_ref,
                        "head_sha": request.head_sha,
                        "base_ref": request.base_ref,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )

    @staticmethod
    def _identity_values(identity: ChildPRIdentity) -> tuple[object, ...]:
        return (
            identity.repository,
            identity.parent_issue,
            identity.issue_number,
            identity.delivery,
            identity.work_id,
        )

    def record_runner_pr_creation(
        self,
        *,
        identity: ChildPRIdentity,
        intent_key: str,
        pr_number: int,
        head_ref: str,
        head_sha: str,
        base_ref: str,
        marker: str,
    ) -> None:
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO child_pr_creations(
                        repository, parent_issue, issue_number, delivery, work_id,
                        intent_key, pr_number, head_ref, head_sha, base_ref,
                        ownership_marker
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(repository, parent_issue, issue_number, delivery, work_id)
                    DO NOTHING
                    """,
                    (
                        *self._identity_values(identity),
                        intent_key,
                        pr_number,
                        head_ref,
                        head_sha,
                        base_ref,
                        marker,
                    ),
                )
                row = connection.execute(
                    """
                    SELECT * FROM child_pr_creations
                    WHERE repository = ? AND parent_issue = ? AND issue_number = ?
                      AND delivery = ? AND work_id = ?
                    """,
                    self._identity_values(identity),
                ).fetchone()
                expected = (intent_key, pr_number, head_ref, head_sha, base_ref, marker)
                if (
                    row is None
                    or tuple(
                        row[name]
                        for name in (
                            "intent_key",
                            "pr_number",
                            "head_ref",
                            "head_sha",
                            "base_ref",
                            "ownership_marker",
                        )
                    )
                    != expected
                ):
                    raise ValueError(
                        "child PR identity already has different creation evidence"
                    )
        finally:
            connection.close()

    def runner_created_pr(
        self, identity: ChildPRIdentity, candidate: ChildPullRequest
    ) -> bool:
        connection = connect(self.path)
        try:
            row = connection.execute(
                """
                SELECT * FROM child_pr_creations
                WHERE repository = ? AND parent_issue = ? AND issue_number = ?
                  AND delivery = ? AND work_id = ? AND pr_number = ?
                """,
                (*self._identity_values(identity), candidate.number),
            ).fetchone()
            return row is not None and (
                row["head_ref"] == candidate.head_ref
                and row["head_sha"] == candidate.head_sha
                and row["base_ref"] == candidate.base_ref
                and row["ownership_marker"] in candidate.body
            )
        finally:
            connection.close()

    def record_abandonment(
        self, identity: ChildPRIdentity, pr_number: int, reason: str
    ) -> None:
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO child_pr_abandonments(
                        repository, parent_issue, issue_number, delivery, work_id,
                        pr_number, reason
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(repository, parent_issue, issue_number, delivery, work_id)
                    DO UPDATE SET pr_number = excluded.pr_number, reason = excluded.reason
                    """,
                    (*self._identity_values(identity), pr_number, reason),
                )
        finally:
            connection.close()

    def abandonment(self, identity: ChildPRIdentity) -> tuple[int, str] | None:
        connection = connect(self.path)
        try:
            row = connection.execute(
                """
                SELECT pr_number, reason FROM child_pr_abandonments
                WHERE repository = ? AND parent_issue = ? AND issue_number = ?
                  AND delivery = ? AND work_id = ? AND resumed_at IS NULL
                """,
                self._identity_values(identity),
            ).fetchone()
            return None if row is None else (row["pr_number"], row["reason"])
        finally:
            connection.close()

    def clear_abandonment(self, identity: ChildPRIdentity) -> None:
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    """
                    UPDATE child_pr_abandonments SET resumed_at = CURRENT_TIMESTAMP
                    WHERE repository = ? AND parent_issue = ? AND issue_number = ?
                      AND delivery = ? AND work_id = ?
                    """,
                    self._identity_values(identity),
                )
        finally:
            connection.close()

    def abandonment_was_retried(self, identity: ChildPRIdentity) -> bool:
        connection = connect(self.path)
        try:
            row = connection.execute(
                """
                SELECT 1 FROM child_pr_abandonments
                WHERE repository = ? AND parent_issue = ? AND issue_number = ?
                  AND delivery = ? AND work_id = ? AND resumed_at IS NOT NULL
                """,
                self._identity_values(identity),
            ).fetchone()
            return row is not None
        finally:
            connection.close()

    def record_process(
        self,
        *,
        worktree: Path,
        identity: ProcessIdentity,
        state: ActionState,
    ) -> None:
        canonical_worktree = str(worktree.resolve())
        connection = connect(self.path)
        try:
            with transaction(connection):
                connection.execute(
                    """
                    INSERT INTO owned_processes(
                        worktree, pid, process_created_at, session_id, state
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(worktree) DO UPDATE SET
                        pid = excluded.pid,
                        process_created_at = excluded.process_created_at,
                        session_id = excluded.session_id,
                        state = excluded.state,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        canonical_worktree,
                        identity.pid,
                        identity.created_at,
                        identity.session_id,
                        state.value,
                    ),
                )
        finally:
            connection.close()

    def unresolved_processes(self) -> tuple[ProcessRecord, ...]:
        connection = connect(self.path)
        try:
            rows = connection.execute(
                """
                SELECT * FROM owned_processes
                WHERE state IN ('pending', 'running', 'ambiguous')
                ORDER BY worktree
                """
            ).fetchall()
            return tuple(
                ProcessRecord(
                    worktree=Path(row["worktree"]),
                    identity=ProcessIdentity(
                        pid=row["pid"],
                        created_at=row["process_created_at"],
                        session_id=row["session_id"],
                    ),
                    state=ActionState(row["state"]),
                )
                for row in rows
            )
        finally:
            connection.close()
