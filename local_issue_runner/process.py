from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


class CommandLaunchError(RuntimeError):
    """A local command could not be started or did not finish in time."""


@dataclass(frozen=True, slots=True)
class CapturedCommand:
    exit_code: int
    stdout: str
    stderr: str


def run_captured(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_seconds: int | None = None,
) -> CapturedCommand:
    if not argv:
        raise ValueError("command arguments must not be empty")
    try:
        completed = subprocess.run(
            tuple(argv),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CommandLaunchError(str(error)) from error
    return CapturedCommand(
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
