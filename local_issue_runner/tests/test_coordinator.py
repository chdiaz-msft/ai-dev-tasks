from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from local_issue_runner.db import CoordinatorStore
from local_issue_runner.locking import (
    LockUnavailable,
    RepositoryMutationLock,
    WindowsJobObject,
)
from local_issue_runner.models import (
    ActionIntent,
    ActionState,
    ControlKind,
    ControlScope,
    ProcessIdentity,
)
from local_issue_runner.reconcile import (
    Coordinator,
    CoordinatorServices,
    InterruptController,
    RecoveryBlocked,
)


@dataclass
class FakeGit:
    common_directory: Path

    def canonical_common_directory(self, repository: Path) -> Path:
        del repository
        return self.common_directory.resolve()


@dataclass
class FakePlanner:
    transitions: list[str]
    calls: int = 0

    def select(self) -> str | None:
        self.calls += 1
        return self.transitions.pop(0) if self.transitions else None


@dataclass
class FakeSupervisor:
    launched: list[str] = field(default_factory=list)
    waited: list[str] = field(default_factory=list)
    graceful: list[str] = field(default_factory=list)
    terminated: list[str] = field(default_factory=list)

    def launch(self, transition: str) -> str:
        self.launched.append(transition)
        return f"job:{transition}"

    def wait(self, job: str) -> None:
        self.waited.append(job)

    def request_graceful_cancellation(self, job: str) -> None:
        self.graceful.append(job)

    def terminate_owned_tree(self, job: str) -> None:
        self.terminated.append(job)


def make_services(
    planner: FakePlanner | None = None,
    supervisor: FakeSupervisor | None = None,
) -> CoordinatorServices:
    return CoordinatorServices(
        planner=planner or FakePlanner([]),
        supervisor=supervisor or FakeSupervisor(),
    )


def test_only_one_mutating_runner_locks_canonical_git_common_directory(
    tmp_path: Path,
) -> None:
    common = tmp_path / "repo" / ".git"
    common.mkdir(parents=True)
    first_checkout = tmp_path / "repo"
    linked_checkout = tmp_path / "linked-worktree"
    linked_checkout.mkdir()
    git = FakeGit(common)

    first = RepositoryMutationLock.for_repository(first_checkout, git=git)
    second = RepositoryMutationLock.for_repository(linked_checkout, git=git)

    with first, pytest.raises(LockUnavailable, match="already"):
        second.acquire()

    with second:
        assert second.held


def test_status_does_not_acquire_the_mutation_lock(tmp_path: Path) -> None:
    store = CoordinatorStore(tmp_path / "runner.db")
    coordinator = Coordinator(store, make_services())
    lock = RepositoryMutationLock(tmp_path / "mutation.lock")

    with lock:
        status = coordinator.status()

    assert status.paused is False
    assert status.active_jobs == ()


def test_control_requests_commit_transactionally_while_runner_holds_lock(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runner.db"
    executor_store = CoordinatorStore(database)
    operator_store = CoordinatorStore(database)
    lock = RepositoryMutationLock(tmp_path / "mutation.lock")
    scope = ControlScope(parent_issue=100, issue_number=101)

    with lock:
        request = operator_store.record_control(ControlKind.PAUSE, scope)
        assert executor_store.pending_controls() == (request,)
        executor_store.apply_pending_controls()

    reopened = CoordinatorStore(database)
    assert reopened.pending_controls() == ()
    assert reopened.pause_state(scope).paused is True


def test_run_once_selects_and_supervises_at_most_one_transition(
    tmp_path: Path,
) -> None:
    planner = FakePlanner(["start:101", "start:102"])
    supervisor = FakeSupervisor()
    coordinator = Coordinator(
        CoordinatorStore(tmp_path / "runner.db"),
        make_services(planner, supervisor),
    )

    observed = coordinator.run(once=True)

    assert observed.transition == "start:101"
    assert planner.calls == 1
    assert supervisor.launched == ["start:101"]
    assert supervisor.waited == ["job:start:101"]
    assert "start:102" in planner.transitions


def test_pause_and_resume_persist_across_coordinator_restarts(tmp_path: Path) -> None:
    database = tmp_path / "runner.db"
    scope = ControlScope(parent_issue=100)
    first = CoordinatorStore(database)
    first.record_control(ControlKind.PAUSE, scope)
    first.apply_pending_controls()

    restarted = CoordinatorStore(database)
    assert restarted.pause_state(scope).paused is True

    restarted.record_control(ControlKind.RESUME, scope)
    restarted.apply_pending_controls()

    assert CoordinatorStore(database).pause_state(scope).paused is False


def test_first_interrupt_requests_graceful_cancellation_and_stops_dispatch(
    tmp_path: Path,
) -> None:
    supervisor = FakeSupervisor()
    controller = InterruptController(supervisor)
    controller.track("owned-job")

    controller.interrupt()

    assert controller.dispatch_allowed is False
    assert supervisor.graceful == ["owned-job"]
    assert supervisor.terminated == []


def test_second_interrupt_terminates_only_owned_subprocess_trees() -> None:
    supervisor = FakeSupervisor()
    controller = InterruptController(supervisor)
    controller.track("owned-a")
    controller.track("owned-b")

    controller.interrupt()
    controller.interrupt()

    assert supervisor.terminated == ["owned-a", "owned-b"]
    assert "unowned-job" not in supervisor.terminated


@dataclass
class FakeWindowsKernel:
    calls: list[tuple[object, ...]] = field(default_factory=list)

    def create_kill_on_close_job(self) -> int:
        self.calls.append(("create", "kill-on-close"))
        return 41

    def assign_process(self, job_handle: int, process_handle: int) -> None:
        self.calls.append(("assign", job_handle, process_handle))

    def close_handle(self, handle: int) -> None:
        self.calls.append(("close", handle))


def test_windows_job_object_assigns_process_and_kills_tree_on_close() -> None:
    kernel = FakeWindowsKernel()
    job = WindowsJobObject(kernel=kernel)

    job.assign(process_handle=73)
    job.close()

    assert kernel.calls == [
        ("create", "kill-on-close"),
        ("assign", 41, 73),
        ("close", 41),
    ]


def test_restart_recovers_interrupted_action_intent_before_new_dispatch(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runner.db"
    store = CoordinatorStore(database)
    intent = ActionIntent(
        key="owner/project:push:child-101:abc123",
        kind="push",
        target="refs/heads/runner/101",
        expected_input="abc123",
    )
    store.record_action_intent(intent)
    planner = FakePlanner(["start:102"])
    recovered: list[str] = []

    def recover(candidate: ActionIntent) -> ActionState:
        recovered.append(candidate.key)
        return ActionState.SUCCEEDED

    coordinator = Coordinator(
        CoordinatorStore(database),
        make_services(planner),
        recover_intent=recover,
    )
    coordinator.run(once=True)

    assert recovered == [intent.key]
    assert CoordinatorStore(database).action_state(intent.key) is ActionState.SUCCEEDED
    assert planner.calls == 1


def test_restart_refuses_replacement_in_possibly_live_worktree(
    tmp_path: Path,
) -> None:
    database = tmp_path / "runner.db"
    worktree = tmp_path / "worktrees" / "child-101"
    worktree.mkdir(parents=True)
    store = CoordinatorStore(database)
    store.record_process(
        worktree=worktree,
        identity=ProcessIdentity(
            pid=4321,
            created_at="2026-09-25T12:00:00Z",
            session_id="job-object-7",
        ),
        state=ActionState.RUNNING,
    )
    planner = FakePlanner(["start:101"])
    supervisor = FakeSupervisor()
    coordinator = Coordinator(
        CoordinatorStore(database),
        make_services(planner, supervisor),
        process_probe=lambda _identity: None,
    )

    with pytest.raises(RecoveryBlocked, match="may still be in use"):
        coordinator.run(once=True)

    assert planner.calls == 0
    assert supervisor.launched == []
