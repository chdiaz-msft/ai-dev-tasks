from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from local_issue_runner.process import CommandLaunchError, run_captured


class CopilotResultError(ValueError):
    """Copilot did not return the exact delivery-result contract."""


class WorktreeBusyError(RuntimeError):
    """An operation would interfere with a job that owns the worktree."""


def require_single_commit(result: CopilotResult) -> str:
    """Return the sole commit produced by a successful local delivery."""
    if result.outcome != "success":
        raise CopilotResultError(
            f"local delivery requires a successful result, got {result.outcome!r}"
        )
    if len(result.commit_ids) != 1:
        raise CopilotResultError(
            "local delivery requires exactly one commit, "
            f"got {len(result.commit_ids)}"
        )
    return result.commit_ids[0]


class WorktreeJobRegistry:
    """Thread-safe, process-local ownership for worktrees used by coding jobs."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owners: dict[Path, object] = {}

    @staticmethod
    def _canonical(worktree: Path) -> Path:
        return worktree.resolve()

    def claim(self, worktree: Path, owner: object) -> None:
        canonical = self._canonical(worktree)
        with self._lock:
            current = self._owners.get(canonical)
            if current is not None:
                raise WorktreeBusyError(
                    f"worktree {canonical} is still owned by running job {current!r}; "
                    "refusing to start another job"
                )
            self._owners[canonical] = owner

    def release(self, worktree: Path, owner: object) -> None:
        canonical = self._canonical(worktree)
        with self._lock:
            if self._owners.get(canonical) is owner:
                del self._owners[canonical]

    def require_idle(self, worktree: Path, operation: str) -> None:
        canonical = self._canonical(worktree)
        with self._lock:
            owner = self._owners.get(canonical)
        if owner is not None:
            raise WorktreeBusyError(
                f"worktree {canonical} is still owned by running job {owner!r}; "
                f"refusing to {operation}"
            )

    def is_running(self, worktree: Path) -> bool:
        with self._lock:
            return self._canonical(worktree) in self._owners


@dataclass(frozen=True, slots=True)
class CopilotResult:
    outcome: str
    summary: str
    commit_ids: tuple[str, ...]
    validation_summary: str
    feedback_dispositions: tuple[str, ...]
    blockers: tuple[str, ...]


_RESULT_FIELDS = {
    "outcome",
    "summary",
    "commit_ids",
    "validation_summary",
    "feedback_dispositions",
    "blockers",
}
_OUTCOMES = {"success", "failed", "blocked", "cancelled", "no_change"}


def parse_copilot_result(raw: str) -> CopilotResult:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CopilotResultError(f"Copilot result is not valid JSON: {error}") from error
    if not isinstance(value, dict) or set(value) != _RESULT_FIELDS:
        raise CopilotResultError("Copilot result does not match the required fields")
    outcome = value["outcome"]
    scalar_fields = ("summary", "validation_summary")
    list_fields = ("commit_ids", "feedback_dispositions", "blockers")
    if outcome not in _OUTCOMES:
        raise CopilotResultError(f"unsupported Copilot outcome: {outcome!r}")
    if any(not isinstance(value[field], str) or not value[field] for field in scalar_fields):
        raise CopilotResultError("summary fields must be non-empty strings")
    if any(
        not isinstance(value[field], list)
        or any(not isinstance(item, str) or not item for item in value[field])
        for field in list_fields
    ):
        raise CopilotResultError("result collection fields must contain only strings")
    if outcome == "success" and not value["commit_ids"]:
        raise CopilotResultError("successful implementation requires at least one commit")
    if outcome == "blocked" and not value["blockers"]:
        raise CopilotResultError("blocked result requires at least one blocker")
    return CopilotResult(
        outcome=outcome,
        summary=value["summary"],
        commit_ids=tuple(value["commit_ids"]),
        validation_summary=value["validation_summary"],
        feedback_dispositions=tuple(value["feedback_dispositions"]),
        blockers=tuple(value["blockers"]),
    )


@dataclass(frozen=True, slots=True)
class CopilotRun:
    result: CopilotResult
    stdout_log: Path
    stderr_log: Path


class CopilotLauncher:
    def __init__(self, log_root: Path, *, timeout_seconds: int = 3600) -> None:
        self.log_root = log_root
        self.timeout_seconds = timeout_seconds

    def __call__(self, worktree: Path, scope_json: bytes) -> str:
        self.log_root.mkdir(parents=True, exist_ok=True)
        prompt = (
            "Implement exactly the child delivery described by this canonical scope "
            "manifest. Work noninteractively, make ordinary commits, and finish by "
            "printing only the required JSON result object:\n"
            + scope_json.decode("utf-8")
        )
        try:
            completed = run_captured(
                (
                    "copilot",
                    "--prompt",
                    prompt,
                    "--allow-all-tools",
                    "--no-ask-user",
                    "--silent",
                ),
                cwd=worktree,
                timeout_seconds=self.timeout_seconds,
            )
        except CommandLaunchError as error:
            raise CopilotResultError(f"Copilot job could not complete: {error}") from error
        (self.log_root / "copilot.stdout.log").write_text(completed.stdout, encoding="utf-8")
        (self.log_root / "copilot.stderr.log").write_text(completed.stderr, encoding="utf-8")
        if completed.exit_code:
            raise CopilotResultError(
                f"Copilot exited with status {completed.exit_code}; inspect captured logs"
            )
        return completed.stdout.strip()


class DeliveryAgent:
    def __init__(
        self,
        *,
        launcher: Callable[[Path, bytes], str],
        worktree_jobs: WorktreeJobRegistry | None = None,
    ) -> None:
        self._launcher = launcher
        self._results: dict[str, tuple[str, CopilotResult]] = {}
        self._lock = threading.Lock()
        self.worktree_jobs = worktree_jobs or WorktreeJobRegistry()

    def run_once(
        self, delivery_key: str, worktree: Path, scope_json: bytes
    ) -> CopilotResult:
        scope_fingerprint = hashlib.sha256(scope_json).hexdigest()
        with self._lock:
            previous = self._results.get(delivery_key)
        if previous is not None and previous[0] == scope_fingerprint:
            return previous[1]
        owner = object()
        self.worktree_jobs.claim(worktree, owner)
        try:
            result = parse_copilot_result(self._launcher(worktree, scope_json))
            with self._lock:
                self._results[delivery_key] = (scope_fingerprint, result)
            return result
        finally:
            self.worktree_jobs.release(worktree, owner)

    def invalidate(self, delivery_key: str) -> None:
        """Forget a completed result before restarting against changed scope."""
        with self._lock:
            self._results.pop(delivery_key, None)
