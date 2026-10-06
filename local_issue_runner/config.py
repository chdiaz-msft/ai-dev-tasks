from __future__ import annotations

import re
from pathlib import Path
from typing import Any, NoReturn

import tomllib

from local_issue_runner.models import (
    AutonomyLevel,
    DeliveryMode,
    ParentConfig,
    RunnerConfig,
)


class ConfigError(ValueError):
    """The runner configuration is missing, malformed, or internally inconsistent."""


_DEFAULTS = {
    "poll_seconds": 300,
    "agent_timeout_seconds": 3600,
    "max_active_issues_per_feature": 5,
    "ci_wait_timeout_seconds": 3600,
    "merge_quiet_seconds": 3600,
}
_ROOT_KEYS = {
    "version",
    "repository",
    "repository_path",
    "remote",
    "main_branch",
    "delivery_mode",
    "poll_seconds",
    "agent_timeout_seconds",
    "max_active_issues_per_feature",
    "ci_wait_timeout_seconds",
    "merge_quiet_seconds",
    "autonomy",
    "validation_commands",
    "required_checks",
    "parents",
}
_PARENT_KEYS = {"issue", "feature_branch", "child_issues"}
_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _fail(message: str) -> NoReturn:
    raise ConfigError(message)


def _string(data: dict[str, Any], key: str, context: str = "configuration") -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        _fail(f"{context}.{key} must be a non-empty string")
    return value


def _positive_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key, _DEFAULTS[key])
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _fail(f"configuration.{key} must be a positive integer")
    return value


def _issue_number(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _fail(f"{field} must be a positive integer issue number")
    return value


def _validate_branch(branch: str, field: str) -> None:
    invalid = (
        branch.startswith(("/", "."))
        or branch.endswith(("/", ".", ".lock"))
        or "//" in branch
        or ".." in branch
        or "@{" in branch
        or any(character.isspace() or character in r"~^:?*[\\" for character in branch)
    )
    if invalid:
        _fail(f"{field} contains an invalid Git branch name: {branch!r}")


def _resolve_path(value: str | Path, *, relative_to: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() and relative_to is not None:
        path = relative_to / path
    return path.resolve()


def _parse_commands(value: object) -> tuple[tuple[str, ...], ...]:
    if not isinstance(value, list) or not value:
        _fail("configuration must define at least one validation command")
    commands: list[tuple[str, ...]] = []
    for index, command in enumerate(value):
        if not isinstance(command, list) or not command:
            _fail(f"validation command {index + 1} must be a non-empty array")
        if any(not isinstance(argument, str) or not argument for argument in command):
            _fail(f"validation command {index + 1} arguments must be non-empty strings")
        executable, *arguments = command
        # Literal TOML strings are convenient for Windows paths, while Python
        # repr-based config generators may double their separators.
        if re.match(r"^[A-Za-z]:\\\\", executable):
            executable = executable.replace("\\\\", "\\")
        commands.append((executable, *arguments))
    return tuple(commands)


def _parse_checks(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        _fail("configuration.required_checks must be an array of strings")
    if any(not isinstance(check, str) or not check.strip() for check in value):
        _fail("configuration.required_checks entries must be non-empty strings")
    checks = tuple(value)
    if len(checks) != len(set(checks)):
        _fail("configuration.required_checks must not contain duplicates")
    return checks


def _parse_parents(value: object, main_branch: str) -> tuple[ParentConfig, ...]:
    if not isinstance(value, list) or not value:
        _fail("configuration.parents must contain at least one parent")

    parents: list[ParentConfig] = []
    parent_issues: set[int] = set()
    branch_owners: dict[str, int] = {}
    child_owners: dict[int, int] = {}
    for index, raw_parent in enumerate(value):
        context = f"parents[{index}]"
        if not isinstance(raw_parent, dict):
            _fail(f"{context} must be a TOML table")
        unknown = set(raw_parent) - _PARENT_KEYS
        if unknown:
            _fail(f"{context} contains unknown field(s): {', '.join(sorted(unknown))}")

        issue = _issue_number(raw_parent.get("issue"), f"{context}.issue")
        if issue in parent_issues:
            _fail(f"parent issue {issue} is configured more than once")
        parent_issues.add(issue)

        branch = _string(raw_parent, "feature_branch", context)
        _validate_branch(branch, f"{context}.feature_branch")
        if branch == main_branch:
            _fail(f"parent issue {issue} feature branch cannot be main branch {main_branch!r}")
        if branch in branch_owners:
            _fail(
                f"feature branch {branch!r} is shared by parent issues "
                f"{branch_owners[branch]} and {issue}"
            )
        branch_owners[branch] = issue

        children: tuple[int, ...] | None = None
        if "child_issues" in raw_parent:
            raw_children = raw_parent["child_issues"]
            if not isinstance(raw_children, list) or not raw_children:
                _fail(f"{context}.child_issues must be a non-empty array when specified")
            parsed = tuple(
                _issue_number(child, f"{context}.child_issues[{child_index}]")
                for child_index, child in enumerate(raw_children)
            )
            if len(parsed) != len(set(parsed)):
                _fail(f"{context}.child_issues contains a duplicate child issue")
            for child in parsed:
                previous = child_owners.get(child)
                if previous is not None:
                    _fail(
                        f"child issue {child} belongs to both parent issues {previous} and {issue}"
                    )
                child_owners[child] = issue
            children = parsed
        parents.append(ParentConfig(issue=issue, feature_branch=branch, child_issues=children))

    nested = parent_issues.intersection(child_owners)
    if nested:
        issue = min(nested)
        _fail(
            f"issue {issue} is both a configured parent and a child issue; "
            "only one parent-to-child level is supported"
        )
    return tuple(parents)


def load_config(path: str | Path) -> RunnerConfig:
    config_path = _resolve_path(path)
    try:
        with config_path.open("rb") as stream:
            raw = tomllib.load(stream)
    except FileNotFoundError as error:
        raise ConfigError(f"configuration file does not exist: {config_path}") from error
    except OSError as error:
        raise ConfigError(f"cannot read configuration file {config_path}: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"invalid TOML in {config_path}: {error}") from error

    unknown = set(raw) - _ROOT_KEYS
    if unknown:
        _fail(f"configuration contains unknown field(s): {', '.join(sorted(unknown))}")
    version = raw.get("version")
    if isinstance(version, bool) or version != 1:
        _fail(f"configuration.version must be 1, got {version!r}")

    repository = _string(raw, "repository")
    if not _REPOSITORY_PATTERN.fullmatch(repository):
        _fail("configuration.repository must use the 'owner/repository' form")
    repository_path_value = _string(raw, "repository_path")
    repository_path = _resolve_path(repository_path_value, relative_to=config_path.parent)

    remote = _string(raw, "remote")
    main_branch = _string(raw, "main_branch")
    _validate_branch(main_branch, "configuration.main_branch")
    autonomy_value = raw.get("autonomy", AutonomyLevel.CREATE_PR.value)
    if not isinstance(autonomy_value, str):
        _fail("configuration.autonomy must be a string")
    try:
        autonomy = AutonomyLevel(autonomy_value)
    except ValueError:
        allowed = ", ".join(level.value for level in AutonomyLevel)
        _fail(f"configuration.autonomy must be one of: {allowed}")
    delivery_mode_value = raw.get("delivery_mode", DeliveryMode.CHILD_PRS.value)
    if not isinstance(delivery_mode_value, str):
        _fail("configuration.delivery_mode must be a string")
    try:
        delivery_mode = DeliveryMode(delivery_mode_value)
    except ValueError:
        allowed = ", ".join(mode.value for mode in DeliveryMode)
        _fail(f"configuration.delivery_mode must be one of: {allowed}")

    return RunnerConfig(
        version=version,
        repository=repository,
        repository_path=repository_path,
        remote=remote,
        main_branch=main_branch,
        delivery_mode=delivery_mode,
        poll_seconds=_positive_int(raw, "poll_seconds"),
        agent_timeout_seconds=_positive_int(raw, "agent_timeout_seconds"),
        max_active_issues_per_feature=_positive_int(raw, "max_active_issues_per_feature"),
        ci_wait_timeout_seconds=_positive_int(raw, "ci_wait_timeout_seconds"),
        merge_quiet_seconds=_positive_int(raw, "merge_quiet_seconds"),
        autonomy=autonomy,
        validation_commands=_parse_commands(raw.get("validation_commands")),
        required_checks=_parse_checks(raw.get("required_checks", [])),
        parents=_parse_parents(raw.get("parents"), main_branch),
        config_path=config_path,
    )
