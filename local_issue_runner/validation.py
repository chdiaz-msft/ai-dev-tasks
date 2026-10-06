from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from local_issue_runner.checks import (
    FailureCategory,
    FailureObservation,
    problem_identity,
)
from local_issue_runner.process import CommandLaunchError, run_captured


class ValidationError(RuntimeError):
    """Validation could not establish evidence for the committed tree."""


class TrackedTreeChanged(ValidationError):
    """The tracked worktree or committed identity changed during validation."""


@dataclass(frozen=True, slots=True)
class ValidationCommand:
    argv: tuple[str, ...]
    working_directory: str = "."
    environment_inputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.argv or any(not argument for argument in self.argv):
            raise ValueError("validation command arguments must not be empty")
        directory = Path(self.working_directory)
        if directory.is_absolute() or ".." in directory.parts:
            raise ValueError("validation working directory must remain in the worktree")
        if len(self.environment_inputs) != len(set(self.environment_inputs)):
            raise ValueError("validation environment inputs must not contain duplicates")
        if any(not name or "=" in name for name in self.environment_inputs):
            raise ValueError("validation environment input names are invalid")


@dataclass(frozen=True, slots=True)
class ValidationCommandResult:
    argv: tuple[str, ...]
    working_directory: str
    exit_code: int
    stdout: str
    stderr: str
    stdout_log: Path | None = None
    stderr_log: Path | None = None


@dataclass(frozen=True, slots=True)
class ValidationRecord:
    head_commit: str
    tree_id: str
    base_commit: str
    scope_fingerprint: str
    validation_config_fingerprint: str
    commands: tuple[ValidationCommandResult, ...]
    evidence_path: Path | None = None

    @property
    def passed(self) -> bool:
        return all(command.exit_code == 0 for command in self.commands)


class ValidationGit(Protocol):
    def tracked_changes(self, worktree: Path) -> tuple[str, ...]: ...

    def head_commit(self, worktree: Path) -> str: ...

    def tree_id(self, worktree: Path, revision: str) -> str: ...


class LocalValidationGit:
    def _run(self, worktree: Path, *arguments: str) -> str:
        try:
            completed = subprocess.run(
                ("git", "-C", str(worktree), *arguments),
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValidationError(f"could not inspect validation worktree: {error}") from error
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise ValidationError(detail or "Git worktree inspection failed")
        return completed.stdout.strip()

    def tracked_changes(self, worktree: Path) -> tuple[str, ...]:
        output = self._run(
            worktree, "status", "--porcelain=v1", "-z", "--untracked-files=no"
        )
        if not output:
            return ()
        entries = output.split("\0")
        return tuple(entry[3:] for entry in entries if len(entry) >= 4)

    def head_commit(self, worktree: Path) -> str:
        return self._run(worktree, "rev-parse", "--verify", "HEAD^{commit}")

    def tree_id(self, worktree: Path, revision: str) -> str:
        return self._run(worktree, "rev-parse", "--verify", f"{revision}^{{tree}}")


CommandRunner = Callable[[Path, tuple[str, ...]], tuple[int, str, str]]


def _run_command(worktree: Path, argv: tuple[str, ...]) -> tuple[int, str, str]:
    try:
        completed = run_captured(argv, cwd=worktree)
    except CommandLaunchError as error:
        raise ValidationError(f"could not run validation command {argv[0]!r}: {error}") from error
    return completed.exit_code, completed.stdout, completed.stderr


class ExactTreeValidator:
    def __init__(
        self,
        *,
        git: ValidationGit | None = None,
        run_command: CommandRunner | None = None,
        evidence_root: Path | None = None,
    ) -> None:
        self._git = git or LocalValidationGit()
        self._run_command = run_command or _run_command
        self._evidence_root = evidence_root

    def validate(
        self,
        *,
        worktree: Path,
        base_commit: str,
        scope_fingerprint: str,
        commands: tuple[ValidationCommand, ...],
    ) -> ValidationRecord:
        if not commands:
            raise ValueError("at least one validation command is required")
        self._assert_clean(worktree, "before validation")
        head = self._git.head_commit(worktree)
        tree = self._git.tree_id(worktree, head)
        config_fingerprint = self._config_fingerprint(commands)
        results: list[ValidationCommandResult] = []

        for index, command in enumerate(commands, start=1):
            command_directory = (worktree / command.working_directory).resolve()
            if not command_directory.is_relative_to(worktree.resolve()):
                raise ValueError("validation working directory escapes the worktree")
            exit_code, stdout, stderr = self._run_command(
                command_directory, command.argv
            )
            stdout_log, stderr_log = self._write_logs(
                head, scope_fingerprint, config_fingerprint, index, stdout, stderr
            )
            results.append(
                ValidationCommandResult(
                    argv=command.argv,
                    working_directory=command.working_directory,
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    stdout_log=stdout_log,
                    stderr_log=stderr_log,
                )
            )
            self._assert_same_tree(worktree, head, tree)

        record = ValidationRecord(
            head_commit=head,
            tree_id=tree,
            base_commit=base_commit,
            scope_fingerprint=scope_fingerprint,
            validation_config_fingerprint=config_fingerprint,
            commands=tuple(results),
        )
        return self._persist_record(record)

    def validate_aggregate(
        self,
        *,
        worktree: Path,
        base_commit: str,
        scope_fingerprint: str,
        commands: tuple[ValidationCommand, ...],
        expected_head: str,
    ) -> ValidationRecord:
        """Validate an integration tree while pinning it to the published head."""
        actual_head = self._git.head_commit(worktree)
        if actual_head != expected_head:
            raise TrackedTreeChanged(
                f"integration head {actual_head} does not match expected {expected_head}"
            )
        return self.validate(
            worktree=worktree,
            base_commit=base_commit,
            scope_fingerprint=scope_fingerprint,
            commands=commands,
        )

    def _assert_clean(self, worktree: Path, phase: str) -> None:
        changed = self._git.tracked_changes(worktree)
        if changed:
            raise TrackedTreeChanged(
                f"tracked files changed {phase}: {', '.join(changed)}"
            )

    def _assert_same_tree(self, worktree: Path, head: str, tree: str) -> None:
        self._assert_clean(worktree, "during validation")
        current_head = self._git.head_commit(worktree)
        current_tree = self._git.tree_id(worktree, current_head)
        if current_head != head or current_tree != tree:
            raise TrackedTreeChanged(
                "HEAD or its committed tree changed during validation"
            )

    @staticmethod
    def _config_fingerprint(commands: tuple[ValidationCommand, ...]) -> str:
        effective = [
            {
                "argv": list(command.argv),
                "working_directory": command.working_directory,
                "environment_inputs": {
                    name: hashlib.sha256(
                        os.environ.get(name, "").encode("utf-8")
                    ).hexdigest()
                    for name in sorted(command.environment_inputs)
                },
            }
            for command in commands
        ]
        canonical = json.dumps(
            effective, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _evidence_directory(
        self, head: str, scope: str, config: str
    ) -> Path | None:
        if self._evidence_root is None:
            return None
        identity = hashlib.sha256(
            f"{head}\0{scope}\0{config}".encode()
        ).hexdigest()
        directory = self._evidence_root / identity
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def _write_logs(
        self,
        head: str,
        scope: str,
        config: str,
        index: int,
        stdout: str,
        stderr: str,
    ) -> tuple[Path | None, Path | None]:
        directory = self._evidence_directory(head, scope, config)
        if directory is None:
            return None, None
        stdout_path = directory / f"command-{index}.stdout.log"
        stderr_path = directory / f"command-{index}.stderr.log"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        return stdout_path, stderr_path

    def _persist_record(self, record: ValidationRecord) -> ValidationRecord:
        directory = self._evidence_directory(
            record.head_commit,
            record.scope_fingerprint,
            record.validation_config_fingerprint,
        )
        if directory is None:
            return record
        path = directory / "validation.json"
        payload = asdict(record)
        payload["evidence_path"] = str(path)
        for command in payload["commands"]:
            for key in ("stdout_log", "stderr_log"):
                value = command[key]
                command[key] = None if value is None else str(value)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(path)
        return ValidationRecord(
            head_commit=record.head_commit,
            tree_id=record.tree_id,
            base_commit=record.base_commit,
            scope_fingerprint=record.scope_fingerprint,
            validation_config_fingerprint=record.validation_config_fingerprint,
            commands=record.commands,
            evidence_path=path,
        )


def validation_failure(record: ValidationRecord) -> FailureObservation | None:
    """Return an actionable failure only when exact-tree local validation failed."""
    failed = tuple(command for command in record.commands if command.exit_code != 0)
    if not failed:
        return None
    identities = ",".join(
        f"{command.working_directory}:{' '.join(command.argv)}" for command in failed
    )
    return FailureObservation(
        identity=problem_identity(
            "local-validation", record.scope_fingerprint, identities
        ),
        category=FailureCategory.LOCAL_VALIDATION,
        detail=f"{len(failed)} local validation command(s) failed for {record.head_commit}",
    )
