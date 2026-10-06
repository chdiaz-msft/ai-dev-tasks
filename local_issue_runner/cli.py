from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from local_issue_runner.config import ConfigError, load_config
from local_issue_runner.db import CoordinatorStore, SnapshotStore
from local_issue_runner.git_ops import GitInspectionError, GitInspector, GitOperations
from local_issue_runner.github_api import (
    GitHubClient,
    GitHubFactReader,
    GitHubInspectionError,
    GitHubInspector,
)
from local_issue_runner.graph import build_feature_snapshots
from local_issue_runner.local_delivery import (
    LocalCommitPlanner,
    LocalCommitSupervisor,
)
from local_issue_runner.locking import LockUnavailable, RepositoryMutationLock
from local_issue_runner.models import (
    ControlKind,
    ControlScope,
    DeliveryMode,
    InterfaceStatus,
    RunnerConfig,
    ToolProbe,
)
from local_issue_runner.parent_validation import (
    ParentIssueInputError,
    render_parent_validation,
    validate_parent_issue,
)
from local_issue_runner.reconcile import (
    Coordinator,
    CoordinatorServices,
    PlanServices,
    RecoveryBlocked,
    plan,
)
from local_issue_runner.scheduler import SnapshotScheduler
from local_issue_runner.status import MergeEligibilityStatus, render_merge_eligibility


class GitService(Protocol):
    def inspect(self, repository_path: Path, remote: str, branches: tuple[str, ...]) -> str: ...


class GitHubService(Protocol):
    def inspect(self, repository: str, branches: tuple[str, ...]) -> object: ...


class AgentService(Protocol):
    def inspect(self) -> None: ...


class ProcessService(Protocol):
    def probe(
        self, argv: tuple[str, ...], *, cwd: Path | None = None, shell: bool = False
    ) -> ToolProbe: ...


@dataclass(frozen=True, slots=True)
class DoctorReport:
    compatible: bool
    probes: tuple[ToolProbe, ...]
    blockers: tuple[str, ...]
    notes: tuple[str, ...]


class ProcessProber:
    def probe(
        self, argv: tuple[str, ...], *, cwd: Path | None = None, shell: bool = False
    ) -> ToolProbe:
        name = argv[0]
        if shell:
            return ToolProbe(name, InterfaceStatus.UNSUPPORTED, detail="shell execution is forbidden")
        executable = name if Path(name).is_file() else shutil.which(name)
        if executable is None:
            return ToolProbe(name, InterfaceStatus.UNSUPPORTED, detail="executable not found")

        command = argv
        if not any(argument in {"--version", "-V", "--help", "-h"} for argument in argv[1:]):
            command = (*argv, "--help")
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return ToolProbe(name, InterfaceStatus.UNREADABLE, detail=str(error))
        output = (completed.stdout or completed.stderr).strip().splitlines()
        detail = output[0] if output else f"exit status {completed.returncode}"
        if completed.returncode not in (0, 1):
            return ToolProbe(name, InterfaceStatus.UNREADABLE, detail=detail)
        version = detail if any(flag in argv for flag in ("--version", "-V")) else None
        return ToolProbe(name, InterfaceStatus.SUPPORTED, version=version, detail=detail)


def effective_run_config(
    config: RunnerConfig, delivery_mode: DeliveryMode | None
) -> RunnerConfig:
    return config if delivery_mode is None else replace(config, delivery_mode=delivery_mode)


class CopilotInspector:
    def inspect(self) -> None:
        try:
            completed = subprocess.run(
                ("copilot", "--help"),
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"could not inspect Copilot authentication: {error}") from error
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(detail or "Copilot CLI interface is unavailable")
        help_output = f"{completed.stdout}\n{completed.stderr}"
        required_options = (
            "--prompt",
            "--allow-all-tools",
            "--no-ask-user",
            "--silent",
        )
        missing = tuple(option for option in required_options if option not in help_output)
        if missing:
            raise RuntimeError(
                "Copilot CLI is missing required noninteractive option(s): "
                + ", ".join(missing)
            )


class _GitCommonDirectory:
    def canonical_common_directory(self, repository: Path) -> Path:
        completed = subprocess.run(
            ("git", "-C", str(repository), "rev-parse", "--git-common-dir"),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise RuntimeError(detail or "could not resolve Git common directory")
        path = Path(completed.stdout.strip())
        return (repository / path).resolve() if not path.is_absolute() else path.resolve()


class _TransitionSupervisor:
    """Stage-three executor seam; concrete delivery actions are added in later slices."""

    def launch(self, transition: object) -> object:
        return transition

    def wait(self, job: object) -> None:
        return None

    def request_graceful_cancellation(self, job: object) -> None:
        return None

    def terminate_owned_tree(self, job: object) -> None:
        return None


@dataclass(frozen=True, slots=True)
class DoctorServices:
    git: GitService
    github: GitHubService
    agent: AgentService
    processes: ProcessService


def _default_services() -> DoctorServices:
    return DoctorServices(GitInspector(), GitHubInspector(), CopilotInspector(), ProcessProber())


def _major(probe: ToolProbe) -> int | None:
    if probe.version is None:
        return None
    match = re.search(r"\d+(?:\.\d+)+", probe.version)
    return int(match.group().split(".", 1)[0]) if match else None


def _record_probe(
    probe: ToolProbe,
    probes: list[ToolProbe],
    blockers: list[str],
    failure: str,
) -> bool:
    probes.append(probe)
    if probe.status is InterfaceStatus.SUPPORTED:
        return True
    blockers.append(f"{failure}: {probe.detail or probe.status}")
    return False


def doctor(
    config_path: str | Path, *, services: DoctorServices | None = None
) -> DoctorReport:
    config = load_config(config_path)
    dependencies = services or _default_services()
    blockers: list[str] = []
    notes: list[str] = []
    probes: list[ToolProbe] = []
    notes.append(f"delivery mode: {config.delivery_mode.value}")
    version_commands = (
        ("git", ("git", "--version"), 2),
        ("gh", ("gh", "--version"), 2),
        ("copilot", ("copilot", "--version"), 1),
        (sys.executable, (sys.executable, "--version"), 3),
    )
    for name, command, expected_major in version_commands:
        probe = dependencies.processes.probe(command)
        supported = _record_probe(
            probe,
            probes,
            blockers,
            f"{name} machine-readable interface is not trusted",
        )
        if supported and probe.version not in (None, "test"):
            major = _major(probe)
            if major != expected_major:
                blockers.append(
                    f"{name} major version {major!r} is unsupported; expected {expected_major}"
                )

    branches = (config.main_branch, *(parent.feature_branch for parent in config.parents))
    try:
        remote_identity = dependencies.git.inspect(
            config.repository_path, config.remote, branches
        )
        if remote_identity.casefold() != config.repository.casefold():
            blockers.append(
                f"remote resolves to {remote_identity}, not configured repository {config.repository}"
            )
        else:
            notes.append(f"repository identity: {remote_identity}")
    except GitInspectionError as error:
        blockers.append(f"Git repository inspection failed: {error}")
    try:
        policies = dependencies.github.inspect(config.repository, branches)
        if isinstance(policies, tuple):
            for policy in policies:
                branch = getattr(policy, "branch", "unknown")
                strict = getattr(policy, "strict", False)
                checks = getattr(policy, "required_checks", ())
                methods = getattr(policy, "merge_methods", ())
                notes.append(
                    f"branch {branch}: strict={strict}, required_checks="
                    f"{','.join(checks) or 'none'}, merge_methods={','.join(methods) or 'none'}"
                )
                if not getattr(policy, "automatic_merge_safe", False):
                    notes.append(
                        f"branch {branch}: PR publication is compatible; automatic merge is blocked"
                    )
    except GitHubInspectionError as error:
        blockers.append(f"GitHub permissions or branch-protection visibility failed: {error}")
    try:
        dependencies.agent.inspect()
    except RuntimeError as error:
        blockers.append(f"Copilot authentication or interface inspection failed: {error}")

    for command in config.validation_commands:
        probe = dependencies.processes.probe(command, cwd=config.repository_path, shell=False)
        if _record_probe(
            probe,
            probes,
            blockers,
            f"validation command {command[0]} cannot start safely",
        ):
            notes.append(f"validation startup: {' '.join(command)}")

    compatible = not blockers
    print("Doctor compatibility report (read-only)")
    for probe in probes:
        print(f"- {probe.name}: {probe.status.value} ({probe.version or probe.detail or 'ok'})")
    for note in notes:
        print(f"- {note}")
    if compatible:
        print("Compatible: mutating commands may proceed under configured autonomy.")
    else:
        print("INCOMPATIBLE: all mutating commands are blocked.")
        for blocker in blockers:
            print(f"- BLOCKER: {blocker}")
    return DoctorReport(compatible, tuple(probes), tuple(blockers), tuple(notes))


def print_merge_eligibility(
    statuses: tuple[MergeEligibilityStatus, ...],
) -> None:
    """Print the read-only merge-gate projection used by the status command."""
    if not statuses:
        print("Merge eligibility: no child or parent PR readiness records")
        return
    for status in statuses:
        for line in render_merge_eligibility(status):
            print(line)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="local-issue-runner")
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--config", required=True, type=Path)
    validate_parent_parser = subparsers.add_parser("validate-parent")
    validate_parent_parser.add_argument("issue_url", metavar="ISSUE_URL")
    validate_parent_parser.add_argument("--json", action="store_true")
    for command in ("plan", "status"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--config", required=True, type=Path)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--config", required=True, type=Path)
    run_parser.add_argument("--once", action="store_true")
    run_parser.add_argument(
        "--delivery-mode",
        type=DeliveryMode,
        choices=tuple(DeliveryMode),
    )
    for command in ("pause", "resume"):
        control_parser = subparsers.add_parser(command)
        control_parser.add_argument("--config", required=True, type=Path)
        control_parser.add_argument("--parent", type=int)
        control_parser.add_argument("--issue", type=int)
    acknowledge_parser = subparsers.add_parser("acknowledge")
    acknowledge_parser.add_argument("--config", required=True, type=Path)
    acknowledge_parser.add_argument("--parent", required=True, type=int)
    acknowledge_parser.add_argument("--issue", required=True, type=int)
    acknowledge_parser.add_argument("--delivery", required=True, type=int)
    acknowledge_parser.add_argument("--reason", required=True)
    retry_parser = subparsers.add_parser("retry")
    retry_parser.add_argument("--config", required=True, type=Path)
    retry_parser.add_argument("--problem", required=True)
    retry_parser.add_argument("--reason", required=True)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "validate-parent":
            report = validate_parent_issue(arguments.issue_url)
            if arguments.json:
                print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
            else:
                print(render_parent_validation(report))
            return 0 if report.valid else 1
        if arguments.command == "doctor":
            report = doctor(arguments.config)
            return 0 if report.compatible else 1
        config = load_config(arguments.config)
        if arguments.command == "run":
            config = effective_run_config(config, arguments.delivery_mode)
            print(f"Delivery mode: {config.delivery_mode.value}")
        database = config.repository_path / ".local-issue-runner" / "runner.db"
        if arguments.command == "retry":
            record = CoordinatorStore(database).retry_problem(
                arguments.problem, arguments.reason
            )
            print(
                f"retry problem={record.identity} streak={record.streak} "
                f"result=resumed reason={record.retry_reason}"
            )
            return 0
        if arguments.command == "acknowledge":
            parent = next(
                (candidate for candidate in config.parents
                 if candidate.issue == arguments.parent),
                None,
            )
            if parent is None or (
                parent.child_issues is not None
                and arguments.issue not in parent.child_issues
            ):
                raise ValueError("acknowledgement target is not a configured child")
            if arguments.delivery <= 0:
                raise ValueError("delivery must be positive")
            reason = arguments.reason.strip()
            if not reason:
                raise ValueError("an acknowledgement reason is required")
            store = CoordinatorStore(database)
            if store.has_child_pr_for_delivery(
                config.repository,
                arguments.parent,
                arguments.issue,
                arguments.delivery,
            ):
                raise ValueError(
                    "acknowledgement cannot override a recorded pull request"
                )
            github = GitHubClient()
            if github.list_child_pull_requests(config.repository, arguments.issue):
                raise ValueError(
                    "acknowledgement cannot override an existing pull request"
                )
            issue = GitHubFactReader().get_issue(
                config.repository, arguments.issue
            )
            if issue.state.upper() != "CLOSED":
                raise ValueError(
                    "no-code work must already be closed before acknowledgement"
                )
            delivery_key = (
                f"{config.repository}:{arguments.parent}:"
                f"{arguments.issue}:{arguments.delivery}"
            )
            store.record_acknowledgement(delivery_key, reason)
            print(
                f"acknowledged issue={arguments.issue} "
                f"delivery={arguments.delivery} reason={reason}"
            )
            return 0
        if arguments.command in {"pause", "resume"}:
            scope = ControlScope(arguments.parent, arguments.issue)
            kind = ControlKind(arguments.command)
            request = CoordinatorStore(database).record_control(kind, scope)
            print(
                f"control action={kind.value} scope={scope.key} "
                f"result=queued request={request.request_id}"
            )
            return 0
        coordinator_store = CoordinatorStore(database)
        facts = GitHubFactReader()
        github = GitHubClient()
        git = GitOperations()
        local_planner: LocalCommitPlanner | None = None
        local_supervisor: LocalCommitSupervisor | None = None
        if config.delivery_mode is DeliveryMode.LOCAL_COMMITS:
            local_planner = LocalCommitPlanner(
                config=config,
                facts=facts,
                github=github,
                store=coordinator_store,
                snapshots=SnapshotStore(database),
                git=git,
            )
            local_supervisor = LocalCommitSupervisor(
                config=config,
                facts=facts,
                github=github,
                store=coordinator_store,
                git=git,
            )
        services = PlanServices(
            github=facts,
            snapshots=SnapshotStore(database),
            git=object(),
            agent=object(),
            validation=object(),
            repair_children=coordinator_store.repair_children,
            completion_satisfied=(
                local_planner.completion_satisfied
                if local_planner is not None
                else None
            ),
        )
        if arguments.command in {"plan", "status"}:
            plan(arguments.config, services=services, label=arguments.command.title())
            if arguments.command == "status":
                store = coordinator_store
                state = Coordinator(
                    store,
                    CoordinatorServices(SnapshotScheduler(lambda: ()), _TransitionSupervisor()),
                ).status()
                print(
                    f"Coordinator: {'paused' if state.paused else 'running'}; "
                    f"active jobs={len(state.active_jobs)}"
                )
                for blocker in store.blockers():
                    print(
                        f"BLOCKER [{blocker.category.value}] "
                        f"{blocker.identity}: {blocker.detail}"
                    )
                if local_supervisor is not None:
                    for feature in local_supervisor.feature_statuses():
                        print(
                            f"Local feature parent={feature.parent_issue} "
                            f"branch={feature.feature_branch} "
                            f"head={feature.local_head or 'missing'} "
                            f"remote={feature.remote_head or 'missing'} "
                            f"unpublished={feature.unpublished_commits} "
                            f"publication_pending={str(feature.publication_pending).lower()}"
                        )
                print_merge_eligibility(())
            return 0

        if local_planner is not None and local_supervisor is not None:
            planner = local_planner
            supervisor = local_supervisor
            recover_intent = local_supervisor.recover_intent
        else:
            planner = SnapshotScheduler(
                lambda: build_feature_snapshots(
                    config,
                    facts,
                    repair_children=coordinator_store.repair_children,
                )
            )
            supervisor = _TransitionSupervisor()
            recover_intent = None
        coordinator = Coordinator(
            coordinator_store,
            CoordinatorServices(planner, supervisor),
            recover_intent=recover_intent,
            poll_seconds=config.poll_seconds,
        )
        lock = RepositoryMutationLock.for_repository(
            config.repository_path, git=_GitCommonDirectory()
        )
        with lock:
            coordinator.run(once=arguments.once)
        return 0
    except ParentIssueInputError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    except GitHubInspectionError as error:
        print(f"ERROR: could not validate parent issue: {error}", file=sys.stderr)
        return 2
    except (ConfigError, ValueError) as error:
        parser.error(str(error))
    except (LockUnavailable, RecoveryBlocked) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 1
    return 2
