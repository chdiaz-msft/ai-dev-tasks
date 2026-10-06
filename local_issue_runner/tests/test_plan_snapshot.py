from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from local_issue_runner.config import load_config
from local_issue_runner.db import SnapshotStore
from local_issue_runner.graph import build_feature_snapshots
from local_issue_runner.models import (
    BlockerCode,
    DependencyFact,
    DependencyRead,
    DiscoveryMode,
    FeatureSnapshot,
    IssueFact,
    TaskKind,
    TransitionKind,
)
from local_issue_runner.reconcile import PlanServices, plan


def write_config(
    path: Path,
    *,
    parents: str = """
[[parents]]
issue = 100
feature_branch = "feature/one"

[[parents]]
issue = 200
feature_branch = "feature/two"
child_issues = [201, 202]
""",
) -> Path:
    path.write_text(
        f"""
version = 1
repository = "owner/project"
repository_path = "repository"
remote = "origin"
main_branch = "main"
autonomy = "create_pr"
validation_commands = [["uv", "run", "pytest"]]
required_checks = []
{parents}
""",
        encoding="utf-8",
    )
    return path


def issue(number: int, *, parent: int | None = None) -> IssueFact:
    return IssueFact(
        repository="owner/project",
        number=number,
        title=f"Issue {number}",
        state="OPEN",
        parent_issue=parent,
    )


def test_issue_fact_body_defaults_empty_for_existing_fixtures() -> None:
    existing = issue(101)
    populated = IssueFact(
        repository="owner/project",
        number=102,
        title="Issue 102",
        state="OPEN",
        body="Implementation requirements",
    )

    assert existing.body == ""
    assert populated.body == "Implementation requirements"


def dependency(prerequisite: int, dependent: int, *, repository: str = "owner/project") -> DependencyFact:
    return DependencyFact(
        repository=repository,
        prerequisite=prerequisite,
        dependent=dependent,
    )


@dataclass
class RecordingFacts:
    issues: dict[int, IssueFact]
    native_children: dict[int, tuple[int, ...]]
    dependencies: dict[int, DependencyRead] = field(default_factory=dict)
    calls: list[tuple[str, int]] = field(default_factory=list)
    mutations: list[object] = field(default_factory=list)

    def list_sub_issues(self, repository: str, parent: int) -> tuple[int, ...]:
        assert repository == "owner/project"
        self.calls.append(("list_sub_issues", parent))
        return self.native_children[parent]

    def get_issue(self, repository: str, number: int) -> IssueFact:
        assert repository == "owner/project"
        self.calls.append(("get_issue", number))
        return self.issues[number]

    def read_dependencies(self, repository: str, number: int) -> DependencyRead:
        assert repository == "owner/project"
        self.calls.append(("read_dependencies", number))
        return self.dependencies.get(number, DependencyRead(complete=True, edges=()))

    def mutate(self, operation: object) -> None:
        self.mutations.append(operation)
        raise AssertionError("plan must not mutate GitHub")


def snapshots_for(config_path: Path, facts: RecordingFacts) -> tuple[FeatureSnapshot, ...]:
    return build_feature_snapshots(load_config(config_path), facts)


def test_native_discovery_and_explicit_children_are_not_combined(tmp_path: Path) -> None:
    config_path = write_config(tmp_path / "runner.toml")
    facts = RecordingFacts(
        issues={
            100: issue(100),
            101: issue(101, parent=100),
            102: issue(102, parent=100),
            200: issue(200),
            201: issue(201, parent=200),
            202: issue(202, parent=200),
            299: issue(299, parent=200),
        },
        native_children={100: (101, 102), 200: (299,)},
    )

    native, explicit = snapshots_for(config_path, facts)

    assert native.discovery_mode is DiscoveryMode.NATIVE
    assert native.child_issues == (101, 102)
    assert explicit.discovery_mode is DiscoveryMode.EXPLICIT
    assert explicit.child_issues == (201, 202)
    assert ("list_sub_issues", 100) in facts.calls
    assert ("list_sub_issues", 200) not in facts.calls
    assert ("get_issue", 299) not in facts.calls


def test_native_discovery_rejects_nested_sub_issues(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path / "runner.toml",
        parents="""
[[parents]]
issue = 100
feature_branch = "feature/one"
""",
    )
    facts = RecordingFacts(
        issues={
            100: issue(100),
            101: issue(101, parent=100),
            111: issue(111, parent=101),
        },
        native_children={100: (101,), 101: (111,)},
    )

    snapshot = snapshots_for(config_path, facts)[0]

    assert snapshot.child_issues == (101,)
    assert any(blocker.code is BlockerCode.NESTED_CHILD for blocker in snapshot.blockers)
    assert all(task.issue_number != 111 for task in snapshot.tasks)


def test_duplicate_native_child_across_parents_blocks_both_features(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path / "runner.toml",
        parents="""
[[parents]]
issue = 100
feature_branch = "feature/one"
[[parents]]
issue = 200
feature_branch = "feature/two"
""",
    )
    facts = RecordingFacts(
        issues={100: issue(100), 200: issue(200), 101: issue(101)},
        native_children={100: (101,), 200: (101,)},
    )

    snapshots = snapshots_for(config_path, facts)

    for snapshot in snapshots:
        blocker = next(
            blocker
            for blocker in snapshot.blockers
            if blocker.code is BlockerCode.DUPLICATE_CHILD
        )
        assert blocker.issue_numbers == (101,)
        assert snapshot.ready_issue_numbers == ()


def test_every_selected_child_requires_a_complete_dependency_read(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path / "runner.toml",
        parents="""
[[parents]]
issue = 100
feature_branch = "feature/one"
child_issues = [101, 102]
""",
    )
    facts = RecordingFacts(
        issues={100: issue(100), 101: issue(101, parent=100), 102: issue(102, parent=100)},
        native_children={},
        dependencies={
            101: DependencyRead(complete=True, edges=()),
            102: DependencyRead(complete=False, edges=(), detail="page 2 unavailable"),
        },
    )

    snapshot = snapshots_for(config_path, facts)[0]

    assert {call for call in facts.calls if call[0] == "read_dependencies"} == {
        ("read_dependencies", 101),
        ("read_dependencies", 102),
    }
    blocker = next(
        blocker
        for blocker in snapshot.blockers
        if blocker.code is BlockerCode.INCOMPLETE_DEPENDENCY_READ
    )
    assert blocker.issue_numbers == (102,)
    assert "page 2 unavailable" in blocker.detail
    assert 102 not in snapshot.ready_issue_numbers


def test_edge_a_to_b_means_b_depends_on_a_and_integration_depends_on_all_children(
    tmp_path: Path,
) -> None:
    config_path = write_config(
        tmp_path / "runner.toml",
        parents="""
[[parents]]
issue = 100
feature_branch = "feature/one"
child_issues = [101, 102, 103]
""",
    )
    facts = RecordingFacts(
        issues={
            100: issue(100),
            101: issue(101, parent=100),
            102: issue(102, parent=100),
            103: issue(103, parent=100),
        },
        native_children={},
        dependencies={
            102: DependencyRead(complete=True, edges=(dependency(101, 102),)),
        },
    )

    snapshot = snapshots_for(config_path, facts)[0]

    integration = next(task for task in snapshot.tasks if task.kind is TaskKind.INTEGRATION)
    assert set(snapshot.predecessors[102]) == {101}
    assert 102 not in snapshot.ready_issue_numbers
    assert set(snapshot.ready_issue_numbers) == {101, 103}
    assert set(snapshot.predecessors[integration.task_id]) == {101, 102, 103}
    assert integration.issue_number is None


@pytest.mark.parametrize(
    ("bad_edge", "code"),
    [
        (dependency(201, 101), BlockerCode.CROSS_FEATURE_DEPENDENCY),
        (dependency(999, 101), BlockerCode.OUT_OF_SCOPE_DEPENDENCY),
        (
            dependency(101, 101, repository="other/project"),
            BlockerCode.CROSS_REPOSITORY_DEPENDENCY,
        ),
    ],
)
def test_unsupported_dependency_edges_block_affected_work(
    tmp_path: Path, bad_edge: DependencyFact, code: BlockerCode
) -> None:
    config_path = write_config(tmp_path / "runner.toml")
    facts = RecordingFacts(
        issues={
            100: issue(100),
            101: issue(101, parent=100),
            200: issue(200),
            201: issue(201, parent=200),
            202: issue(202, parent=200),
        },
        native_children={100: (101,)},
        dependencies={101: DependencyRead(complete=True, edges=(bad_edge,))},
    )

    first = snapshots_for(config_path, facts)[0]

    assert any(blocker.code is code for blocker in first.blockers)
    assert 101 not in first.ready_issue_numbers


def test_cycle_blocks_cycle_and_downstream_but_keeps_unaffected_work_ready(tmp_path: Path) -> None:
    config_path = write_config(
        tmp_path / "runner.toml",
        parents="""
[[parents]]
issue = 100
feature_branch = "feature/one"
child_issues = [101, 102, 103, 104]
""",
    )
    facts = RecordingFacts(
        issues={100: issue(100), **{number: issue(number, parent=100) for number in range(101, 105)}},
        native_children={},
        dependencies={
            101: DependencyRead(complete=True, edges=(dependency(102, 101),)),
            102: DependencyRead(complete=True, edges=(dependency(101, 102),)),
            103: DependencyRead(complete=True, edges=(dependency(102, 103),)),
        },
    )

    snapshot = snapshots_for(config_path, facts)[0]

    cycle = next(blocker for blocker in snapshot.blockers if blocker.code is BlockerCode.CYCLE)
    assert set(cycle.issue_numbers) == {101, 102}
    assert set(snapshot.blocked_issue_numbers) >= {101, 102, 103}
    assert snapshot.ready_issue_numbers == (104,)


def test_complete_snapshot_replacement_is_atomic(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "runner.db")
    original = FeatureSnapshot.fixture(parent_issue=100, child_issues=(101,))
    replacement = replace(original, child_issues=(102,))
    store.replace_all((original,))

    def interrupted() -> Iterable[FeatureSnapshot]:
        yield replacement
        raise RuntimeError("fact read interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        store.replace_all(interrupted())

    assert store.load_all() == (original,)
    store.replace_all((replacement,))
    assert store.load_all() == (replacement,)


@dataclass
class NoSideEffectService:
    calls: list[object] = field(default_factory=list)

    def __getattr__(self, name: str) -> object:
        def forbidden(*args: object, **kwargs: object) -> None:
            self.calls.append((name, args, kwargs))
            raise AssertionError(f"read-only plan attempted {name}")

        return forbidden


def test_plan_reads_facts_and_prints_next_transition_without_side_effects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = write_config(
        tmp_path / "runner.toml",
        parents="""
[[parents]]
issue = 100
feature_branch = "feature/one"
child_issues = [101, 102]
""",
    )
    facts = RecordingFacts(
        issues={100: issue(100), 101: issue(101, parent=100), 102: issue(102, parent=100)},
        native_children={},
        dependencies={
            102: DependencyRead(complete=True, edges=(dependency(101, 102),)),
        },
    )
    forbidden = NoSideEffectService()
    services = PlanServices(
        github=facts,
        snapshots=SnapshotStore(tmp_path / "runner.db"),
        git=forbidden,
        agent=forbidden,
        validation=forbidden,
    )

    report = plan(config_path, services=services)

    assert report.next_transition.kind is TransitionKind.START_CHILD
    assert report.next_transition.issue_number == 101
    output = capsys.readouterr().out
    assert "explicit" in output.lower()
    assert "101, 102" in output
    assert "integration" in output.lower()
    assert "101" in output
    assert forbidden.calls == []
    assert facts.mutations == []
