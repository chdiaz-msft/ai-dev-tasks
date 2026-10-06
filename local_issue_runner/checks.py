from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path

from local_issue_runner.db import CoordinatorStore
from local_issue_runner.models import ProblemCategory, ProblemRecord


def problem_identity(namespace: str, *components: str) -> str:
    """Build a stable identity from facts that survive delivery and head changes."""
    parts = tuple(part.strip() for part in (namespace, *components))
    if any(not part for part in parts):
        raise ValueError("problem identity components must not be empty")
    return ":".join(parts)


class CheckState(StrEnum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    WAITING = "waiting"
    PASSED = "passed"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"


class CheckConclusion(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    NEUTRAL = "neutral"
    SKIPPED = "skipped"


class FailureCategory(StrEnum):
    CODE = "code"
    LOCAL_VALIDATION = "local_validation"
    INFRASTRUCTURE = "infrastructure"
    CREDENTIALS = "credentials"
    UNRELATED = "unrelated"
    UNCLEAR_REQUIREMENT = "unclear_requirement"


class RepairAction(StrEnum):
    NONE = "none"
    START_REPAIR = "start_repair"
    WAIT = "wait"
    NEEDS_HUMAN = "needs_human"


@dataclass(frozen=True, slots=True)
class CheckObservation:
    name: str
    state: CheckState
    conclusion: CheckConclusion | None
    source: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("check names must not be empty")
        if self.source not in {"check_run", "status_context"}:
            raise ValueError("check source must be check_run or status_context")
        if self.state is CheckState.COMPLETED and self.conclusion is None:
            raise ValueError("completed checks require a conclusion")


@dataclass(frozen=True, slots=True)
class FailureObservation:
    identity: str
    category: FailureCategory
    detail: str

    def __post_init__(self) -> None:
        if not self.identity.strip() or not self.detail.strip():
            raise ValueError("failure identity and detail are required")


@dataclass(frozen=True, slots=True)
class CheckDecision:
    state: CheckState
    blocking_checks: tuple[str, ...] = ()
    optional_checks: tuple[str, ...] = ()
    wait_deadline: datetime | None = None
    repair_action: RepairAction = RepairAction.NONE


@dataclass(frozen=True, slots=True)
class RequiredCheckFacts:
    required: tuple[str, ...]
    passing: tuple[str, ...]


def required_check_facts(
    required_checks: Iterable[str],
    checks: Iterable[CheckObservation],
) -> RequiredCheckFacts:
    """Project current check observations into immutable merge-gate facts."""
    required = tuple(dict.fromkeys(required_checks))
    if any(not name.strip() for name in required):
        raise ValueError("required check names must not be empty")
    observations = _checks_by_name(checks)
    passing = tuple(
        name
        for name in required
        if _has_conclusion(observations.get(name, ()), frozenset({CheckConclusion.SUCCESS}))
    )
    return RequiredCheckFacts(required, passing)


@dataclass(frozen=True, slots=True)
class RepairDecision:
    action: RepairAction
    reason: str
    failure_identity: str | None = None
    resume_at: datetime | None = None
    streak: int = 0
    stopped: bool = False


def _checks_by_name(
    checks: Iterable[CheckObservation],
) -> dict[str, tuple[CheckObservation, ...]]:
    grouped: dict[str, list[CheckObservation]] = {}
    for observation in checks:
        grouped.setdefault(observation.name, []).append(observation)
    return {name: tuple(values) for name, values in grouped.items()}


def _has_conclusion(
    checks: Iterable[CheckObservation], conclusions: frozenset[CheckConclusion]
) -> bool:
    return any(
        check.state is CheckState.COMPLETED and check.conclusion in conclusions
        for check in checks
    )


class CIRepairStore:
    """Small durable store for CI deadlines and unresolved-problem history."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.coordinator = CoordinatorStore(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def deadline(
        self, head_sha: str, check_name: str, *, create: datetime
    ) -> datetime:
        encoded = create.isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO ci_wait_deadlines VALUES (?, ?, ?)",
                (head_sha, check_name, encoded),
            )
            value = connection.execute(
                "SELECT deadline FROM ci_wait_deadlines "
                "WHERE head_sha = ? AND check_name = ?",
                (head_sha, check_name),
            ).fetchone()["deadline"]
        return datetime.fromisoformat(str(value))

    def clear_deadline(self, head_sha: str, check_name: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM ci_wait_deadlines WHERE head_sha = ? AND check_name = ?",
                (head_sha, check_name),
            )

    def increment_problem(self, identity: str) -> ProblemRecord:
        return self.coordinator.record_no_progress(
            identity, ProblemCategory.CODE, "unresolved repair made no verified progress"
        )

    def retry(self, identity: str, reason: str) -> ProblemRecord:
        return self.coordinator.retry_problem(identity, reason)

    def problem(self, identity: str) -> ProblemRecord:
        record = self.coordinator.problem(identity)
        if record is None:
            return ProblemRecord(
                identity, ProblemCategory.CODE, "unresolved problem", 0, False
            )
        return record


class CIRepairPolicy:
    _SUCCESSES = frozenset({CheckConclusion.SUCCESS})
    _FAILURES = frozenset(
        {
            CheckConclusion.FAILURE,
            CheckConclusion.CANCELLED,
            CheckConclusion.TIMED_OUT,
        }
    )
    _ACTIONABLE = frozenset(
        {FailureCategory.CODE, FailureCategory.LOCAL_VALIDATION}
    )
    _INFRASTRUCTURE_DELAYS = (timedelta(minutes=1), timedelta(minutes=5))

    def __init__(
        self,
        *,
        required_checks: tuple[str, ...],
        store: CIRepairStore,
        ci_wait_timeout: timedelta,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if ci_wait_timeout <= timedelta(0):
            raise ValueError("CI wait timeout must be positive")
        if len(required_checks) != len(set(required_checks)):
            raise ValueError("required checks must be unique")
        self.required_checks = required_checks
        self.store = store
        self.ci_wait_timeout = ci_wait_timeout
        self._now = now or (lambda: datetime.now(timezone.utc))

    def evaluate_checks(
        self, *, head_sha: str, checks: Iterable[CheckObservation]
    ) -> CheckDecision:
        if not head_sha.strip():
            raise ValueError("head SHA is required")
        observations = _checks_by_name(checks)
        required = set(self.required_checks)
        optional = tuple(sorted(name for name in observations if name not in required))
        waiting: list[str] = []
        failed: list[str] = []
        deadlines: list[datetime] = []
        now = self._now()
        for name in self.required_checks:
            values = observations.get(name, [])
            if _has_conclusion(values, self._SUCCESSES):
                self.store.clear_deadline(head_sha, name)
                continue
            if _has_conclusion(values, self._FAILURES):
                failed.append(name)
                continue
            waiting.append(name)
            deadlines.append(
                self.store.deadline(
                    head_sha, name, create=now + self.ci_wait_timeout
                )
            )
        if failed:
            return CheckDecision(
                CheckState.BLOCKED, tuple(failed), optional_checks=optional
            )
        if waiting:
            deadline = min(deadlines)
            state = CheckState.NEEDS_HUMAN if now >= deadline else CheckState.WAITING
            return CheckDecision(
                state,
                tuple(waiting),
                optional_checks=optional,
                wait_deadline=deadline,
            )
        return CheckDecision(CheckState.PASSED, optional_checks=optional)

    def plan_failure(
        self, failure: FailureObservation, *, infrastructure_attempt: int = 1
    ) -> RepairDecision:
        if failure.category in self._ACTIONABLE:
            return RepairDecision(
                RepairAction.START_REPAIR,
                "failure is actionable through a code repair",
                failure.identity,
            )
        if failure.category is FailureCategory.INFRASTRUCTURE:
            if 1 <= infrastructure_attempt <= len(self._INFRASTRUCTURE_DELAYS):
                return RepairDecision(
                    RepairAction.WAIT,
                    "waiting before bounded infrastructure retry",
                    failure.identity,
                    self._now()
                    + self._INFRASTRUCTURE_DELAYS[infrastructure_attempt - 1],
                )
            decision = RepairDecision(
                RepairAction.NEEDS_HUMAN,
                "infrastructure retry budget exhausted",
                failure.identity,
            )
            self.store.coordinator.record_blocker(
                failure.identity, ProblemCategory.INFRASTRUCTURE, failure.detail
            )
            return decision
        self.store.coordinator.record_blocker(
            failure.identity, ProblemCategory(failure.category.value), failure.detail
        )
        return RepairDecision(
            RepairAction.NEEDS_HUMAN,
            f"{failure.category.value} failures are not safe automatic repairs",
            failure.identity,
        )

    def rate_limit_wait(self, *, reset_at: datetime) -> RepairDecision:
        now = self._now()
        resume_at = reset_at if reset_at > now else now + timedelta(seconds=1)
        return RepairDecision(
            RepairAction.WAIT, "waiting for GitHub rate-limit reset", resume_at=resume_at
        )

    def record_no_progress(
        self, *, problem_identity: str, delivery: int, repair_child: int | None
    ) -> RepairDecision:
        if delivery <= 0 or (repair_child is not None and repair_child <= 0):
            raise ValueError("delivery and repair child numbers must be positive")
        record = self.store.increment_problem(problem_identity)
        return RepairDecision(
            RepairAction.NEEDS_HUMAN if record.stopped else RepairAction.START_REPAIR,
            "repeated unresolved problem" if record.stopped else "repair may continue",
            problem_identity,
            streak=record.streak,
            stopped=record.stopped,
        )

    def retry(self, *, problem_identity: str, reason: str) -> RepairDecision:
        record = self.store.retry(problem_identity, reason)
        return RepairDecision(
            RepairAction.START_REPAIR,
            reason,
            problem_identity,
            streak=record.streak,
            stopped=record.stopped,
        )
