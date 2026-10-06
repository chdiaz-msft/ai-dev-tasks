from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 11

_SCHEMA_V1 = """
CREATE TABLE repositories (
    id INTEGER PRIMARY KEY,
    canonical_identity TEXT NOT NULL UNIQUE,
    configuration_identity TEXT
);

CREATE TABLE features (
    id INTEGER PRIMARY KEY,
    repository_id INTEGER NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    parent_issue INTEGER NOT NULL CHECK (parent_issue > 0),
    feature_branch TEXT NOT NULL,
    discovery_mode TEXT NOT NULL CHECK (discovery_mode IN ('native', 'explicit')),
    last_served_position INTEGER,
    UNIQUE (repository_id, parent_issue),
    UNIQUE (repository_id, feature_branch)
);

CREATE TABLE graph_snapshots (
    id INTEGER PRIMARY KEY,
    feature_id INTEGER NOT NULL REFERENCES features(id) ON DELETE CASCADE,
    source_revision TEXT,
    is_complete INTEGER NOT NULL CHECK (is_complete IN (0, 1)),
    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
    captured_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (id, feature_id)
);
CREATE UNIQUE INDEX one_current_snapshot_per_feature
    ON graph_snapshots(feature_id) WHERE is_current = 1;

CREATE TABLE snapshot_tasks (
    snapshot_id INTEGER NOT NULL,
    feature_id INTEGER NOT NULL,
    task_id TEXT NOT NULL,
    task_order INTEGER NOT NULL CHECK (task_order >= 0),
    kind TEXT NOT NULL CHECK (kind IN ('child', 'integration')),
    issue_number INTEGER,
    title TEXT,
    source_revision TEXT,
    is_ready INTEGER NOT NULL CHECK (is_ready IN (0, 1)),
    is_blocked INTEGER NOT NULL CHECK (is_blocked IN (0, 1)),
    PRIMARY KEY (snapshot_id, feature_id, task_id),
    UNIQUE (snapshot_id, feature_id, task_order),
    FOREIGN KEY (snapshot_id, feature_id)
        REFERENCES graph_snapshots(id, feature_id) ON DELETE CASCADE,
    CHECK (
        (kind = 'child' AND issue_number IS NOT NULL) OR
        (kind = 'integration' AND issue_number IS NULL)
    )
);

CREATE TABLE snapshot_memberships (
    snapshot_id INTEGER NOT NULL,
    feature_id INTEGER NOT NULL,
    membership_order INTEGER NOT NULL CHECK (membership_order >= 0),
    issue_number INTEGER NOT NULL CHECK (issue_number > 0),
    PRIMARY KEY (snapshot_id, feature_id, issue_number),
    UNIQUE (snapshot_id, feature_id, membership_order),
    FOREIGN KEY (snapshot_id, feature_id)
        REFERENCES graph_snapshots(id, feature_id) ON DELETE CASCADE
);

CREATE TABLE dependency_edges (
    snapshot_id INTEGER NOT NULL,
    feature_id INTEGER NOT NULL,
    prerequisite_task_id TEXT NOT NULL,
    dependent_task_id TEXT NOT NULL,
    source_relationship_id TEXT,
    source_revision TEXT,
    PRIMARY KEY (
        snapshot_id, feature_id, prerequisite_task_id, dependent_task_id
    ),
    FOREIGN KEY (snapshot_id, feature_id, prerequisite_task_id)
        REFERENCES snapshot_tasks(snapshot_id, feature_id, task_id)
        ON DELETE CASCADE,
    FOREIGN KEY (snapshot_id, feature_id, dependent_task_id)
        REFERENCES snapshot_tasks(snapshot_id, feature_id, task_id)
        ON DELETE CASCADE,
    CHECK (prerequisite_task_id <> dependent_task_id)
);

CREATE TABLE snapshot_blockers (
    snapshot_id INTEGER NOT NULL,
    feature_id INTEGER NOT NULL,
    blocker_order INTEGER NOT NULL CHECK (blocker_order >= 0),
    code TEXT NOT NULL,
    detail TEXT NOT NULL,
    issue_numbers TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, feature_id, blocker_order),
    FOREIGN KEY (snapshot_id, feature_id)
        REFERENCES graph_snapshots(id, feature_id) ON DELETE CASCADE
);
"""

_SCHEMA_V2 = """
CREATE TABLE control_requests (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('pause', 'resume')),
    scope_key TEXT NOT NULL,
    parent_issue INTEGER,
    issue_number INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    applied_at TEXT,
    CHECK (parent_issue IS NULL OR parent_issue > 0),
    CHECK (issue_number IS NULL OR issue_number > 0),
    CHECK (issue_number IS NULL OR parent_issue IS NOT NULL)
);
CREATE INDEX pending_control_requests
    ON control_requests(id) WHERE applied_at IS NULL;

CREATE TABLE pause_states (
    scope_key TEXT PRIMARY KEY,
    parent_issue INTEGER,
    issue_number INTEGER,
    paused INTEGER NOT NULL CHECK (paused IN (0, 1)),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (parent_issue IS NULL OR parent_issue > 0),
    CHECK (issue_number IS NULL OR issue_number > 0),
    CHECK (issue_number IS NULL OR parent_issue IS NOT NULL)
);

CREATE TABLE action_intents (
    intent_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    target TEXT NOT NULL,
    expected_input TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'running', 'succeeded', 'failed', 'ambiguous')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE action_outcomes (
    intent_key TEXT PRIMARY KEY
        REFERENCES action_intents(intent_key) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK (state IN ('succeeded', 'failed', 'ambiguous')),
    detail TEXT,
    observed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE owned_processes (
    worktree TEXT PRIMARY KEY,
    pid INTEGER NOT NULL CHECK (pid > 0),
    process_created_at TEXT NOT NULL,
    session_id TEXT NOT NULL,
    state TEXT NOT NULL
        CHECK (state IN ('pending', 'running', 'succeeded', 'failed', 'ambiguous')),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_SCHEMA_V3 = """
CREATE TABLE child_pr_creations (
    repository TEXT NOT NULL,
    parent_issue INTEGER NOT NULL,
    issue_number INTEGER NOT NULL,
    delivery INTEGER NOT NULL,
    work_id TEXT NOT NULL,
    intent_key TEXT NOT NULL,
    pr_number INTEGER NOT NULL CHECK (pr_number > 0),
    head_ref TEXT NOT NULL,
    head_sha TEXT NOT NULL,
    base_ref TEXT NOT NULL,
    ownership_marker TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (repository, parent_issue, issue_number, delivery, work_id),
    UNIQUE (repository, pr_number),
    UNIQUE (intent_key)
);

CREATE TABLE child_pr_abandonments (
    repository TEXT NOT NULL,
    parent_issue INTEGER NOT NULL,
    issue_number INTEGER NOT NULL,
    delivery INTEGER NOT NULL,
    work_id TEXT NOT NULL,
    pr_number INTEGER NOT NULL CHECK (pr_number > 0),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resumed_at TEXT,
    PRIMARY KEY (repository, parent_issue, issue_number, delivery, work_id)
);
"""

_SCHEMA_V4 = """
CREATE TABLE completion_evidence (
    delivery_key TEXT PRIMARY KEY,
    repository TEXT NOT NULL,
    parent_issue INTEGER NOT NULL CHECK (parent_issue > 0),
    issue_number INTEGER NOT NULL CHECK (issue_number > 0),
    delivery INTEGER NOT NULL CHECK (delivery > 0),
    pr_number INTEGER NOT NULL CHECK (pr_number > 0),
    base_ref TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    work_head TEXT NOT NULL,
    scope_fingerprint TEXT NOT NULL,
    resulting_commit TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE completion_acknowledgements (
    delivery_key TEXT PRIMARY KEY,
    reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_SCHEMA_V5 = """
CREATE TABLE issue_deliveries (
    delivery_key TEXT PRIMARY KEY,
    repository TEXT NOT NULL,
    parent_issue INTEGER NOT NULL CHECK (parent_issue > 0),
    issue_number INTEGER NOT NULL CHECK (issue_number > 0),
    delivery INTEGER NOT NULL CHECK (delivery > 0),
    scope_fingerprint TEXT NOT NULL,
    branch TEXT NOT NULL,
    worktree TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (repository, parent_issue, issue_number, delivery)
);

CREATE TABLE parent_deliveries (
    delivery_key TEXT PRIMARY KEY,
    repository TEXT NOT NULL,
    parent_issue INTEGER NOT NULL CHECK (parent_issue > 0),
    delivery INTEGER NOT NULL CHECK (delivery > 0),
    selected_children TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (repository, parent_issue, delivery)
);

CREATE TABLE dependency_invalidations (
    repository TEXT NOT NULL,
    parent_issue INTEGER NOT NULL CHECK (parent_issue > 0),
    prerequisite_issue INTEGER NOT NULL CHECK (prerequisite_issue > 0),
    dependent_issue INTEGER NOT NULL CHECK (dependent_issue > 0),
    prerequisite_delivery INTEGER NOT NULL CHECK (prerequisite_delivery > 0),
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (
        repository, parent_issue, prerequisite_issue,
        dependent_issue, prerequisite_delivery
    ),
    CHECK (prerequisite_issue <> dependent_issue)
);
"""

_SCHEMA_V6 = """
CREATE TABLE scheduling_positions (
    repository TEXT PRIMARY KEY,
    last_served_parent INTEGER NOT NULL CHECK (last_served_parent > 0),
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_SCHEMA_V7 = """
CREATE TABLE feedback_items (
    feedback_identity TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    item_id TEXT NOT NULL,
    thread_id TEXT,
    latest_revision TEXT NOT NULL,
    latest_thread_state TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE feedback_revisions (
    revision_fingerprint TEXT PRIMARY KEY,
    feedback_identity TEXT NOT NULL
        REFERENCES feedback_items(feedback_identity) ON DELETE CASCADE,
    author_login TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    source_updated_at TEXT NOT NULL,
    thread_state TEXT,
    disposition TEXT,
    reason TEXT,
    handled_at TEXT,
    UNIQUE (feedback_identity, revision_fingerprint)
);

CREATE TABLE feedback_reply_intents (
    revision_fingerprint TEXT PRIMARY KEY
        REFERENCES feedback_revisions(revision_fingerprint) ON DELETE CASCADE,
    pr_number INTEGER NOT NULL CHECK (pr_number > 0),
    reply_body TEXT NOT NULL,
    output_identity TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE feedback_resolution_intents (
    revision_fingerprint TEXT PRIMARY KEY
        REFERENCES feedback_revisions(revision_fingerprint) ON DELETE CASCADE,
    thread_id TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE runner_feedback_outputs (
    feedback_identity TEXT PRIMARY KEY,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE pr_quiet_periods (
    pr_number INTEGER PRIMARY KEY CHECK (pr_number > 0),
    restarted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_revision TEXT NOT NULL
);
"""

_SCHEMA_V8 = """
CREATE TABLE IF NOT EXISTS ci_wait_deadlines (
    head_sha TEXT NOT NULL,
    check_name TEXT NOT NULL,
    deadline TEXT NOT NULL,
    PRIMARY KEY (head_sha, check_name)
);

CREATE TABLE IF NOT EXISTS unresolved_problems (
    identity TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    detail TEXT NOT NULL,
    streak INTEGER NOT NULL DEFAULT 0 CHECK (streak >= 0),
    stopped INTEGER NOT NULL DEFAULT 0 CHECK (stopped IN (0, 1)),
    retry_reason TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS operational_blockers (
    identity TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    detail TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_SCHEMA_V9 = """
CREATE TABLE integration_repair_children (
    repository TEXT NOT NULL,
    parent_issue INTEGER NOT NULL CHECK (parent_issue > 0),
    finding_identity TEXT NOT NULL,
    issue_number INTEGER NOT NULL CHECK (issue_number > 0),
    source TEXT NOT NULL CHECK (length(trim(source)) > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (repository, parent_issue, finding_identity),
    UNIQUE (repository, parent_issue, issue_number)
);
"""

_SCHEMA_V10 = """
CREATE TABLE qualification_receipts (
    id INTEGER PRIMARY KEY,
    level TEXT NOT NULL CHECK (level IN ('child', 'full')),
    disposable_repository TEXT NOT NULL CHECK (
        length(trim(disposable_repository)) > 0
    ),
    exercise_id TEXT NOT NULL CHECK (length(trim(exercise_id)) > 0),
    successful INTEGER NOT NULL CHECK (successful IN (0, 1)),
    observed_transitions TEXT NOT NULL,
    runtime_identity TEXT NOT NULL CHECK (length(trim(runtime_identity)) > 0),
    tool_versions TEXT NOT NULL,
    policy_fingerprint TEXT NOT NULL CHECK (
        length(trim(policy_fingerprint)) > 0
    ),
    completed_at TEXT NOT NULL,
    evidence_paths TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (disposable_repository, exercise_id)
);
CREATE INDEX qualification_receipts_latest
    ON qualification_receipts(level, successful, completed_at DESC, id DESC);
"""

_SCHEMA_V11 = """
CREATE TABLE local_completion_evidence (
    delivery_key TEXT PRIMARY KEY,
    repository TEXT NOT NULL,
    parent_issue INTEGER NOT NULL CHECK (parent_issue > 0),
    issue_number INTEGER NOT NULL CHECK (issue_number > 0),
    delivery INTEGER NOT NULL CHECK (delivery > 0),
    feature_branch TEXT NOT NULL CHECK (length(trim(feature_branch)) > 0),
    feature_base TEXT NOT NULL CHECK (length(trim(feature_base)) > 0),
    resulting_commit TEXT NOT NULL CHECK (length(trim(resulting_commit)) > 0),
    scope_fingerprint TEXT NOT NULL CHECK (
        length(trim(scope_fingerprint)) > 0
    ),
    validation_identity TEXT NOT NULL CHECK (
        length(trim(validation_identity)) > 0
    ),
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (repository, parent_issue, issue_number, delivery)
);
"""


def migrate(connection: sqlite3.Connection) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema version {version} is newer than supported version "
            f"{SCHEMA_VERSION}"
        )
    if version == 0:
        connection.executescript(_SCHEMA_V1)
        connection.execute("PRAGMA user_version = 1")
        version = 1
    if version == 1:
        connection.executescript(_SCHEMA_V2)
        connection.execute("PRAGMA user_version = 2")
        version = 2
    if version == 2:
        connection.executescript(_SCHEMA_V3)
        connection.execute("PRAGMA user_version = 3")
        version = 3
    if version == 3:
        connection.executescript(_SCHEMA_V4)
        connection.execute("PRAGMA user_version = 4")
        version = 4
    if version == 4:
        connection.executescript(_SCHEMA_V5)
        connection.execute("PRAGMA user_version = 5")
        version = 5
    if version == 5:
        connection.executescript(_SCHEMA_V6)
        connection.execute("PRAGMA user_version = 6")
        version = 6
    if version == 6:
        connection.executescript(_SCHEMA_V7)
        connection.execute("PRAGMA user_version = 7")
        version = 7
    if version == 7:
        connection.executescript(_SCHEMA_V8)
        legacy_problems = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = 'table' AND name = 'ci_problems'"
        ).fetchone()
        if legacy_problems is not None:
            connection.execute(
                """
                INSERT OR IGNORE INTO unresolved_problems(
                    identity, category, detail, streak, stopped, retry_reason
                )
                SELECT identity, 'code',
                       'unresolved repair made no verified progress',
                       streak, stopped, retry_reason
                FROM ci_problems
                """
            )
        connection.execute("PRAGMA user_version = 8")
        version = 8
    if version == 8:
        connection.executescript(_SCHEMA_V9)
        connection.execute("PRAGMA user_version = 9")
        version = 9
    if version == 9:
        connection.executescript(_SCHEMA_V10)
        connection.execute("PRAGMA user_version = 10")
        version = 10
    if version == 10:
        connection.executescript(_SCHEMA_V11)
        connection.execute("PRAGMA user_version = 11")
    connection.commit()
