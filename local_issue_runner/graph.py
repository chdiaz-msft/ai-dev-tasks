from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Mapping
from graphlib import CycleError, TopologicalSorter
from typing import Protocol

from local_issue_runner.models import (
    BlockerCode,
    DependencyEdge,
    DependencyRead,
    DiscoveryMode,
    FeatureSnapshot,
    IntegrationRepairChildRecord,
    IssueFact,
    RunnerConfig,
    SnapshotBlocker,
    SnapshotTask,
    TaskId,
    TaskKind,
    build_dependency_maps,
)


class FactReader(Protocol):
    def list_sub_issues(self, repository: str, parent: int) -> tuple[int, ...]: ...

    def get_issue(self, repository: str, number: int) -> IssueFact: ...

    def read_dependencies(self, repository: str, number: int) -> DependencyRead: ...


def _descendants(
    seeds: set[TaskId], successors: Mapping[TaskId, Iterable[TaskId]]
) -> set[TaskId]:
    result = set(seeds)
    pending = deque(seeds)
    while pending:
        for successor in successors.get(pending.popleft(), ()):
            if successor not in result:
                result.add(successor)
                pending.append(successor)
    return result


def invalidated_dependents(
    snapshot: FeatureSnapshot,
    invalidated_issue_numbers: Iterable[int],
    *,
    completed_issue_numbers: Iterable[int] = (),
) -> tuple[int, ...]:
    """Return unfinished descendants whose dependency proof is no longer current."""
    invalidated: set[TaskId] = set(invalidated_issue_numbers)
    completed = set(completed_issue_numbers)
    _, successors = build_dependency_maps(
        (task.task_id for task in snapshot.tasks), snapshot.dependency_edges
    )
    affected = _descendants(invalidated, successors) - invalidated
    return tuple(
        issue
        for issue in snapshot.child_issues
        if issue in affected and issue not in completed
    )


def build_feature_snapshots(
    config: RunnerConfig,
    facts: FactReader,
    *,
    completion_satisfied: Callable[[str, int, int, str], bool] | None = None,
    repair_children: Callable[
        [str, int], tuple[IntegrationRepairChildRecord, ...]
    ]
    | None = None,
) -> tuple[FeatureSnapshot, ...]:
    selected: list[tuple[int, ...]] = []
    modes: list[DiscoveryMode] = []
    for parent in config.parents:
        if parent.child_issues is None:
            children = tuple(dict.fromkeys(facts.list_sub_issues(config.repository, parent.issue)))
            mode = DiscoveryMode.NATIVE
        else:
            children = parent.child_issues
            mode = DiscoveryMode.EXPLICIT
        if repair_children is not None:
            additions = repair_children(config.repository, parent.issue)
            children = tuple(
                dict.fromkeys(
                    (*children, *(addition.issue_number for addition in additions))
                )
            )
        selected.append(children)
        modes.append(mode)

    owners: dict[int, list[int]] = defaultdict(list)
    for parent, children in zip(config.parents, selected, strict=True):
        for child in children:
            owners[child].append(parent.issue)
    all_children = set(owners)

    snapshots: list[FeatureSnapshot] = []
    for parent, children, mode in zip(config.parents, selected, modes, strict=True):
        blockers: list[SnapshotBlocker] = []
        initially_blocked: set[TaskId] = set()
        duplicate = tuple(child for child in children if len(owners[child]) > 1)
        if duplicate:
            blockers.append(
                SnapshotBlocker(
                    BlockerCode.DUPLICATE_CHILD,
                    f"child issue(s) belong to multiple features: {', '.join(map(str, duplicate))}",
                    duplicate,
                )
            )
            initially_blocked.update(children)

        parent_fact = facts.get_issue(config.repository, parent.issue)
        issue_facts = {
            child: facts.get_issue(config.repository, child) for child in children
        }
        if mode is DiscoveryMode.NATIVE:
            nested: list[int] = []
            for child in children:
                try:
                    grandchildren = facts.list_sub_issues(config.repository, child)
                except KeyError:
                    grandchildren = ()
                if grandchildren:
                    nested.append(child)
            if nested:
                nested_tuple = tuple(nested)
                blockers.append(
                    SnapshotBlocker(
                        BlockerCode.NESTED_CHILD,
                        "only one parent-to-child level is supported",
                        nested_tuple,
                    )
                )
                initially_blocked.update(nested_tuple)

        integration_id = f"integration:{parent.issue}"
        tasks = tuple(
            SnapshotTask(
                child,
                TaskKind.CHILD,
                issue_number=child,
                title=issue_facts[child].title,
                source_revision=issue_facts[child].source_revision,
            )
            for child in children
        ) + (SnapshotTask(integration_id, TaskKind.INTEGRATION),)
        edges: list[DependencyEdge] = []
        seen_edges: set[tuple[TaskId, TaskId]] = set()

        for child in children:
            dependency_read = facts.read_dependencies(config.repository, child)
            if not dependency_read.complete:
                detail = dependency_read.detail or "dependency relationship read was incomplete"
                blockers.append(
                    SnapshotBlocker(
                        BlockerCode.INCOMPLETE_DEPENDENCY_READ,
                        f"issue {child}: {detail}",
                        (child,),
                    )
                )
                initially_blocked.add(child)
            for fact in dependency_read.edges:
                affected = fact.dependent if fact.dependent in children else child
                if fact.repository.casefold() != config.repository.casefold():
                    code = BlockerCode.CROSS_REPOSITORY_DEPENDENCY
                elif (
                    fact.prerequisite in all_children
                    and fact.prerequisite not in children
                ) or (
                    fact.dependent in all_children and fact.dependent not in children
                ):
                    code = BlockerCode.CROSS_FEATURE_DEPENDENCY
                elif fact.prerequisite not in children or fact.dependent not in children:
                    code = BlockerCode.OUT_OF_SCOPE_DEPENDENCY
                else:
                    key = (fact.prerequisite, fact.dependent)
                    if key not in seen_edges:
                        seen_edges.add(key)
                        edges.append(
                            DependencyEdge(
                                fact.prerequisite,
                                fact.dependent,
                                fact.relationship_id,
                                fact.source_revision,
                            )
                        )
                    continue
                blockers.append(
                    SnapshotBlocker(
                        code,
                        f"unsupported dependency {fact.prerequisite} -> {fact.dependent}",
                        tuple(
                            number
                            for number in (fact.prerequisite, fact.dependent)
                            if number in children
                        ),
                    )
                )
                initially_blocked.add(affected)

        for child in children:
            edges.append(DependencyEdge(child, integration_id))

        graph, successors = build_dependency_maps(
            (task.task_id for task in tasks), edges
        )

        sorter = TopologicalSorter(graph)
        cycle_nodes: set[TaskId] = set()
        try:
            sorter.prepare()
        except CycleError as error:
            cycle = error.args[1] if len(error.args) > 1 else ()
            cycle_nodes = set(cycle)
            cycle_nodes.discard(integration_id)
            cycle_issues = tuple(
                child for child in children if child in cycle_nodes
            )
            blockers.append(
                SnapshotBlocker(
                    BlockerCode.CYCLE,
                    "dependency cycle: " + " -> ".join(map(str, cycle)),
                    cycle_issues,
                )
            )
            initially_blocked.update(cycle_nodes)

        blocked = _descendants(initially_blocked, successors)
        completed = {
            child
            for child in children
            if completion_satisfied is not None
            and completion_satisfied(
                config.repository, parent.issue, child, parent.feature_branch
            )
        }
        ready = tuple(
            child
            for child in children
            if child not in completed
            and child not in blocked
            and all(
                prerequisite in completed
                for prerequisite in graph[child]
            )
        )
        snapshots.append(
            FeatureSnapshot(
                repository=config.repository,
                parent_issue=parent.issue,
                feature_branch=parent.feature_branch,
                discovery_mode=mode,
                child_issues=children,
                tasks=tasks,
                dependency_edges=tuple(edges),
                source_revision=parent_fact.source_revision,
                blockers=tuple(blockers),
                ready_issue_numbers=ready,
                blocked_issue_numbers=tuple(
                    child for child in children if child in blocked
                ),
            )
        )
    return tuple(snapshots)
