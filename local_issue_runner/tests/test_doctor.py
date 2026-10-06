from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from local_issue_runner.cli import (
    CopilotInspector,
    DoctorServices,
    doctor,
    effective_run_config,
)
from local_issue_runner.config import ConfigError, load_config
from local_issue_runner.models import DeliveryMode, InterfaceStatus, ToolProbe


def write_config(
    path: Path,
    *,
    repository_path: str = "repository",
    delivery_mode: str | None = None,
    validation_commands: str = '[["uv", "run", "pytest"]]',
    parents: str = """
[[parents]]
issue = 100
feature_branch = "feature/one"
child_issues = [101, 102]

[[parents]]
issue = 200
feature_branch = "feature/two"
child_issues = [201]
""",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    delivery_mode_line = (
        "" if delivery_mode is None else f"delivery_mode = {delivery_mode!r}\n"
    )
    path.write_text(
        f"""
version = 1
repository = "owner/project"
repository_path = {repository_path!r}
remote = "origin"
main_branch = "main"
{delivery_mode_line}\
autonomy = "create_pr"
validation_commands = {validation_commands}
required_checks = []
{parents}
""",
        encoding="utf-8",
    )
    return path


def test_loads_valid_config(tmp_path: Path) -> None:
    config_path = write_config(tmp_path / "runner.toml")

    config = load_config(config_path)

    assert config.repository == "owner/project"
    assert config.remote == "origin"
    assert config.main_branch == "main"
    assert config.delivery_mode is DeliveryMode.CHILD_PRS
    assert config.validation_commands == (("uv", "run", "pytest"),)
    assert [(parent.issue, parent.feature_branch) for parent in config.parents] == [
        (100, "feature/one"),
        (200, "feature/two"),
    ]


@pytest.mark.parametrize("delivery_mode", list(DeliveryMode))
def test_loads_explicit_delivery_mode(
    tmp_path: Path, delivery_mode: DeliveryMode
) -> None:
    config_path = write_config(
        tmp_path / "runner.toml", delivery_mode=delivery_mode.value
    )

    config = load_config(config_path)

    assert config.delivery_mode is delivery_mode


def test_run_delivery_mode_override_does_not_mutate_configured_value(
    tmp_path: Path,
) -> None:
    configured = load_config(write_config(tmp_path / "runner.toml"))

    effective = effective_run_config(configured, DeliveryMode.LOCAL_COMMITS)

    assert configured.delivery_mode is DeliveryMode.CHILD_PRS
    assert effective.delivery_mode is DeliveryMode.LOCAL_COMMITS


@pytest.mark.parametrize("delivery_mode", ["unsupported", 1])
def test_rejects_invalid_delivery_mode(
    tmp_path: Path, delivery_mode: str | int
) -> None:
    config_path = write_config(tmp_path / "runner.toml")
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(
        text.replace(
            'main_branch = "main"',
            f"main_branch = \"main\"\ndelivery_mode = {delivery_mode!r}",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="delivery_mode"):
        load_config(config_path)


def test_resolves_repository_path_relative_to_config_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = write_config(tmp_path / "configs" / "runner.toml", repository_path="../repo")
    unrelated_cwd = tmp_path / "elsewhere"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)

    config = load_config(config_path)

    assert config.repository_path == (tmp_path / "repo").resolve()


@pytest.mark.parametrize(
    ("parents", "message"),
    [
        (
            """
[[parents]]
issue = 100
feature_branch = "feature/one"
child_issues = [101]
[[parents]]
issue = 200
feature_branch = "feature/two"
child_issues = [101]
""",
            "child issue 101",
        ),
        (
            """
[[parents]]
issue = 100
feature_branch = "feature/one"
child_issues = []
""",
            "child_issues",
        ),
        (
            """
[[parents]]
issue = 100
feature_branch = "feature/same"
child_issues = [101]
[[parents]]
issue = 200
feature_branch = "feature/same"
child_issues = [201]
""",
            "feature/same",
        ),
        (
            """
[[parents]]
issue = 100
feature_branch = "main"
child_issues = [101]
""",
            "main",
        ),
    ],
)
def test_rejects_invalid_parent_membership_and_branches(
    tmp_path: Path, parents: str, message: str
) -> None:
    config_path = write_config(tmp_path / "runner.toml", parents=parents)

    with pytest.raises(ConfigError, match=message):
        load_config(config_path)


def test_rejects_missing_validation_commands(tmp_path: Path) -> None:
    config_path = write_config(tmp_path / "runner.toml", validation_commands="[]")

    with pytest.raises(ConfigError, match="validation command"):
        load_config(config_path)


@dataclass
class RecordingGit:
    remote_identity: str = "owner/project"
    calls: list[tuple[str, object]] = field(default_factory=list)

    def inspect(self, repository_path: Path, remote: str, branches: tuple[str, ...]) -> str:
        self.calls.append(("inspect", (repository_path, remote, branches)))
        return self.remote_identity

    def write_worktree(self, *_args: object) -> None:
        raise AssertionError("doctor must not write a worktree")


@dataclass
class RecordingGitHub:
    calls: list[tuple[str, object]] = field(default_factory=list)

    def inspect(self, repository: str, branches: tuple[str, ...]) -> None:
        self.calls.append(("inspect", (repository, branches)))

    def mutate(self, *_args: object) -> None:
        raise AssertionError("doctor must not mutate GitHub")


@dataclass
class RecordingAgent:
    calls: list[str] = field(default_factory=list)

    def inspect(self) -> None:
        self.calls.append("inspect")

    def run_paid(self, *_args: object) -> None:
        raise AssertionError("doctor must not make a paid model call")


@dataclass
class RecordingProcesses:
    probes: dict[str, ToolProbe] = field(default_factory=dict)
    calls: list[tuple[tuple[str, ...], Path | None, bool]] = field(default_factory=list)

    def probe(
        self, argv: tuple[str, ...], *, cwd: Path | None = None, shell: bool = False
    ) -> ToolProbe:
        self.calls.append((argv, cwd, shell))
        return self.probes.get(
            argv[0],
            ToolProbe(name=argv[0], status=InterfaceStatus.SUPPORTED, version="test"),
        )


@dataclass(frozen=True, slots=True)
class RecordingDoctorServices(DoctorServices):
    git: RecordingGit
    github: RecordingGitHub
    agent: RecordingAgent
    processes: RecordingProcesses


def services(
    *,
    git: RecordingGit | None = None,
    processes: RecordingProcesses | None = None,
) -> RecordingDoctorServices:
    return RecordingDoctorServices(
        git=git or RecordingGit(),
        github=RecordingGitHub(),
        agent=RecordingAgent(),
        processes=processes or RecordingProcesses(),
    )


def test_rejects_repository_remote_identity_mismatch(tmp_path: Path) -> None:
    config_path = write_config(tmp_path / "runner.toml")

    report = doctor(config_path, services=services(git=RecordingGit("other/project")))

    assert not report.compatible
    assert any("other/project" in blocker and "owner/project" in blocker for blocker in report.blockers)


@pytest.mark.parametrize("tool", ["gh", "git", "copilot", sys.executable])
@pytest.mark.parametrize(
    "status", [InterfaceStatus.UNSUPPORTED, InterfaceStatus.UNREADABLE]
)
def test_blocks_unsupported_or_unreadable_machine_interfaces(
    tmp_path: Path, tool: str, status: InterfaceStatus
) -> None:
    config_path = write_config(tmp_path / "runner.toml")
    processes = RecordingProcesses(
        probes={tool: ToolProbe(name=tool, status=status, detail="fixture failure")}
    )

    report = doctor(config_path, services=services(processes=processes))

    assert not report.compatible
    assert any(tool in blocker and "fixture failure" in blocker for blocker in report.blockers)


def test_validation_startup_preserves_windows_path_with_spaces(tmp_path: Path) -> None:
    interpreter = r"C:\Program Files\Python 3.11\python.exe"
    config_path = write_config(
        tmp_path / "config folder" / "runner.toml",
        repository_path="../repository folder",
        validation_commands=f'[[{interpreter!r}, "-m", "pytest", "--help"]]',
    )
    processes = RecordingProcesses()

    report = doctor(config_path, services=services(processes=processes))

    assert report.compatible
    assert (
        (interpreter, "-m", "pytest", "--help"),
        (tmp_path / "repository folder").resolve(),
        False,
    ) in processes.calls


def test_doctor_is_read_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = write_config(tmp_path / "runner.toml")
    dependencies = services()

    report = doctor(config_path, services=dependencies)

    assert report.compatible
    assert dependencies.github.calls
    assert dependencies.git.calls
    assert dependencies.agent.calls == ["inspect"]
    assert "compatible" in capsys.readouterr().out.lower()


def test_copilot_inspector_probes_current_noninteractive_interface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def fake_run(
        command: tuple[str, ...],
        **_kwargs: object,
    ) -> object:
        calls.append(command)
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": (
                    "Usage: copilot [OPTIONS] [COMMAND]\n"
                    "  -p, --prompt <text>\n"
                    "      --allow-all-tools\n"
                    "      --no-ask-user\n"
                    "  -s, --silent\n"
                ),
                "stderr": "",
            },
        )()

    monkeypatch.setattr("local_issue_runner.cli.subprocess.run", fake_run)

    CopilotInspector().inspect()

    assert calls == [("copilot", "--help")]


def test_copilot_inspector_rejects_missing_noninteractive_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(
        _command: tuple[str, ...],
        **_kwargs: object,
    ) -> object:
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": "Usage: copilot [OPTIONS]\n  --prompt <text>\n",
                "stderr": "",
            },
        )()

    monkeypatch.setattr("local_issue_runner.cli.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="--allow-all-tools"):
        CopilotInspector().inspect()
