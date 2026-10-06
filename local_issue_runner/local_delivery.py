from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from local_issue_runner.agent_backend import (
    CopilotLauncher,
    DeliveryAgent,
    require_single_commit,
)
from local_issue_runner.completion import (
    ChildCompletionService,
    CompletionDelivery,
    CompletionMode,
    LocalCommitCompletionEvidence,
)
from local_issue_runner.db import CoordinatorStore, SnapshotStore
from local_issue_runner.git_ops import (
    FeatureBranchWorkspace,
    GitOperations,
)
from local_issue_runner.github_api import GitHubClient, GitHubFactReader
from local_issue_runner.graph import build_feature_snapshots
from local_issue_runner.integration import (
    IntegrationBlocked,
    IntegrationIdentity,
    IntegrationPullRequest,
    IntegrationRequest,
    integration_pr_owned_by_parent,
)
from local_issue_runner.models import (
    ActionIntent,
    ActionState,
    IssueDeliveryRecord,
    NextTransition,
    ParentConfig,
    ParentDeliveryRecord,
    RunnerConfig,
    TransitionKind,
)
from local_issue_runner.reconcile import RecoveryBlocked, decide_next
from local_issue_runner.scope import (
    ChildScopeInput,
    ScopeManifest,
    build_child_scope_manifest,
)
from local_issue_runner.validation import ExactTreeValidator, ValidationCommand


class LocalDeliveryBlocked(RuntimeError):
    """Local-commit delivery cannot proceed without operator action."""


@dataclass(frozen=True, slots=True)
class LocalFeatureStatus:
    parent_issue: int
    feature_branch: str
    local_head: str | None
    remote_head: str | None
    unpublished_commits: int
    publication_pending: bool


class LocalBranchCompletionGit:
    def __init__(self, config: RunnerConfig, git: GitOperations) -> None:
        self.config = config
        self.git = git

    def commit_available_in_branch(
        self, repository: str, commit: str, branch: str
    ) -> bool:
        if repository.casefold() != self.config.repository.casefold():
            return False
        try:
            head = self.git.resolve_local_branch(self.config.repository_path, branch)
        except Exception:
            return False
        return self.git.is_ancestor(self.config.repository_path, commit, head)


def _parent(config: RunnerConfig, parent_issue: int) -> ParentConfig:
    parent = next(
        (candidate for candidate in config.parents if candidate.issue == parent_issue),
        None,
    )
    if parent is None:
        raise LocalDeliveryBlocked(f"parent issue {parent_issue} is not configured")
    return parent


def _selected_children(
    config: RunnerConfig,
    parent: ParentConfig,
    facts: GitHubFactReader,
    store: CoordinatorStore,
) -> tuple[int, ...]:
    children = (
        facts.list_sub_issues(config.repository, parent.issue)
        if parent.child_issues is None
        else parent.child_issues
    )
    repairs = store.repair_children(config.repository, parent.issue)
    return tuple(
        dict.fromkeys((*children, *(repair.issue_number for repair in repairs)))
    )


def _scope_manifest(
    config: RunnerConfig,
    parent: ParentConfig,
    issue_number: int,
    delivery: int,
    facts: GitHubFactReader,
) -> ScopeManifest:
    issue = facts.get_issue(config.repository, issue_number)
    dependency_read = facts.read_dependencies(config.repository, issue_number)
    if not dependency_read.complete:
        raise LocalDeliveryBlocked(
            f"dependency facts for issue {issue_number} are incomplete"
        )
    prerequisites = tuple(
        {
            "repository": dependency.repository,
            "issue": dependency.prerequisite,
        }
        for dependency in dependency_read.edges
        if dependency.dependent == issue_number
    )
    return build_child_scope_manifest(
        ChildScopeInput(
            repository=config.repository,
            parent_issue=parent.issue,
            issue_number=issue_number,
            delivery=delivery,
            title=issue.title,
            body=issue.body,
            feature_branch=parent.feature_branch,
            prerequisites=prerequisites,
        )
    )


class LocalCommitPlanner:
    def __init__(
        self,
        *,
        config: RunnerConfig,
        facts: GitHubFactReader,
        github: GitHubClient,
        store: CoordinatorStore,
        snapshots: SnapshotStore,
        git: GitOperations,
    ) -> None:
        self.config = config
        self.facts = facts
        self.github = github
        self.store = store
        self.snapshots = snapshots
        self.git = git

    def _latest_delivery(
        self, parent_issue: int, issue_number: int
    ) -> IssueDeliveryRecord | None:
        deliveries = self.store.issue_deliveries(
            self.config.repository, parent_issue, issue_number
        )
        return deliveries[-1] if deliveries else None

    def completion_satisfied(
        self,
        repository: str,
        parent_issue: int,
        issue_number: int,
        feature_branch: str,
    ) -> bool:
        if repository.casefold() != self.config.repository.casefold():
            return False
        latest = self._latest_delivery(parent_issue, issue_number)
        if latest is None:
            return False
        parent = _parent(self.config, parent_issue)
        manifest = _scope_manifest(
            self.config, parent, issue_number, latest.delivery, self.facts
        )
        if manifest.fingerprint != latest.scope_fingerprint:
            return False
        evidence = self.store.completion(latest.key, CompletionMode.LOCAL_COMMIT)
        if not isinstance(evidence, LocalCommitCompletionEvidence):
            return False
        if (
            evidence.feature_branch != feature_branch
            or evidence.scope_fingerprint != manifest.fingerprint
            or self.github.get_issue_state(repository, issue_number) != "CLOSED"
        ):
            return False
        try:
            head = self.git.resolve_local_branch(
                self.config.repository_path, feature_branch
            )
        except Exception:
            return False
        return self.git.is_ancestor(
            self.config.repository_path, evidence.resulting_commit, head
        )

    def _integration_pending(
        self, parent: ParentConfig, children: tuple[int, ...]
    ) -> bool:
        try:
            local_head = self.git.resolve_local_branch(
                self.config.repository_path, parent.feature_branch
            )
        except Exception:
            return False
        remote_head = self.git.remote_branch_head(
            self.config.repository_path,
            self.config.remote,
            parent.feature_branch,
        )
        if local_head != remote_head:
            return True
        candidates = self.github.list_integration_pull_requests(
            self.config.repository,
            parent.feature_branch,
            self.config.main_branch,
        )
        owned = tuple(
            candidate
            for candidate in candidates
            if integration_pr_owned_by_parent(
                candidate.body,
                repository=self.config.repository,
                parent_issue=parent.issue,
            )
        )
        if len(owned) > 1:
            raise IntegrationBlocked("multiple runner-owned integration PRs found")
        if owned:
            if len(candidates) != 1:
                raise IntegrationBlocked(
                    "runner-owned and external integration PRs both exist"
                )
            candidate = owned[0]
            if (
                candidate.head_sha != local_head
                or candidate.base_ref != self.config.main_branch
            ):
                raise IntegrationBlocked(
                    "runner-owned integration PR does not match the published head"
                )
            return False
        if candidates:
            raise IntegrationBlocked("external integration PR observed and not adopted")
        return True

    def select(self) -> NextTransition | None:
        snapshots = build_feature_snapshots(
            self.config,
            self.facts,
            completion_satisfied=self.completion_satisfied,
            repair_children=self.store.repair_children,
        )
        self.snapshots.replace_all(snapshots)
        transition = decide_next(snapshots)
        if transition.kind is not TransitionKind.WAIT:
            return transition
        for snapshot in snapshots:
            if not snapshot.child_issues or snapshot.blockers:
                continue
            if not all(
                self.completion_satisfied(
                    snapshot.repository,
                    snapshot.parent_issue,
                    issue,
                    snapshot.feature_branch,
                )
                for issue in snapshot.child_issues
            ):
                continue
            parent = _parent(self.config, snapshot.parent_issue)
            if self._integration_pending(parent, snapshot.child_issues):
                return NextTransition(
                    TransitionKind.START_INTEGRATION,
                    parent_issue=parent.issue,
                    detail=f"publish feature branch {parent.feature_branch}",
                )
        return None


class LocalCommitSupervisor:
    def __init__(
        self,
        *,
        config: RunnerConfig,
        facts: GitHubFactReader,
        github: GitHubClient,
        store: CoordinatorStore,
        git: GitOperations,
        agent: DeliveryAgent | None = None,
        validator: ExactTreeValidator | None = None,
    ) -> None:
        self.config = config
        self.facts = facts
        self.github = github
        self.store = store
        self.git = git
        self.agent = agent
        self.runtime_root = config.repository_path / ".local-issue-runner"
        self.validator = validator or ExactTreeValidator(
            evidence_root=self.runtime_root / "validation"
        )
        self.workspace = FeatureBranchWorkspace(self.runtime_root, git=git)
        self.completion = ChildCompletionService(
            store=store,
            git=LocalBranchCompletionGit(config, git),
            github=github,
        )

    def launch(self, transition: object) -> object:
        if not isinstance(transition, NextTransition):
            raise TypeError("local transition must be a NextTransition")
        if transition.kind is TransitionKind.START_CHILD:
            if transition.parent_issue is None or transition.issue_number is None:
                raise LocalDeliveryBlocked("child transition has no issue identity")
            self._run_child(transition.parent_issue, transition.issue_number)
        elif transition.kind is TransitionKind.START_INTEGRATION:
            if transition.parent_issue is None:
                raise LocalDeliveryBlocked("integration transition has no parent")
            self._publish_feature(transition.parent_issue)
        elif transition.kind is TransitionKind.BLOCKED:
            raise LocalDeliveryBlocked(transition.detail or "feature graph is blocked")
        else:
            raise LocalDeliveryBlocked(
                f"unsupported local transition: {transition.kind.value}"
            )
        return transition

    def wait(self, job: object) -> None:
        del job

    def request_graceful_cancellation(self, job: object) -> None:
        del job

    def terminate_owned_tree(self, job: object) -> None:
        del job

    def _select_delivery(
        self, parent: ParentConfig, issue_number: int
    ) -> tuple[IssueDeliveryRecord | None, ScopeManifest]:
        deliveries = self.store.issue_deliveries(
            self.config.repository, parent.issue, issue_number
        )
        if not deliveries:
            return None, _scope_manifest(
                self.config, parent, issue_number, 1, self.facts
            )
        latest = deliveries[-1]
        current = _scope_manifest(
            self.config, parent, issue_number, latest.delivery, self.facts
        )
        evidence = self.store.completion(latest.key, CompletionMode.LOCAL_COMMIT)
        action_state = self.store.action_state(f"local-child:{latest.key}")
        if current.fingerprint == latest.scope_fingerprint and (
            evidence is not None
            or action_state not in {ActionState.FAILED, ActionState.AMBIGUOUS}
        ):
            return latest, current
        return None, _scope_manifest(
            self.config, parent, issue_number, latest.delivery + 1, self.facts
        )

    def _run_child(self, parent_issue: int, issue_number: int) -> None:
        parent = _parent(self.config, parent_issue)
        existing, manifest = self._select_delivery(parent, issue_number)
        delivery_number = (
            existing.delivery
            if existing is not None
            else (
                self.store.issue_deliveries(
                    self.config.repository, parent.issue, issue_number
                )[-1].delivery
                + 1
                if self.store.issue_deliveries(
                    self.config.repository, parent.issue, issue_number
                )
                else 1
            )
        )
        remote_head = self.git.remote_branch_head(
            self.config.repository_path,
            self.config.remote,
            parent.feature_branch,
        )
        if remote_head is None:
            raise LocalDeliveryBlocked(
                f"remote feature branch does not exist: {parent.feature_branch}"
            )
        prepared = self.workspace.prepare(
            repository=self.config.repository_path,
            remote=self.config.remote,
            feature_branch=parent.feature_branch,
            parent_issue=parent.issue,
            expected_remote_head=remote_head,
        )
        record = existing or IssueDeliveryRecord(
            self.config.repository,
            parent.issue,
            issue_number,
            delivery_number,
            manifest.fingerprint,
            parent.feature_branch,
            prepared.worktree,
        )
        self.store.record_issue_delivery(record)
        scope_path = (
            self.runtime_root
            / "scopes"
            / f"p-{parent.issue}-i-{issue_number}-d-{delivery_number}.json"
        )
        manifest.persist(scope_path)

        persisted = self.store.completion(record.key, CompletionMode.LOCAL_COMMIT)
        if isinstance(persisted, LocalCommitCompletionEvidence):
            self.completion.reconcile(
                persisted.delivery,
                mode=CompletionMode.LOCAL_COMMIT,
                resulting_commit=persisted.resulting_commit,
                validation_identity=persisted.validation_identity,
            )
            return

        base_head = prepared.head
        intent = ActionIntent(
            key=f"local-child:{record.key}",
            kind="local_child",
            target=parent.feature_branch,
            expected_input=json.dumps(
                {
                    "base_head": base_head,
                    "scope_fingerprint": manifest.fingerprint,
                    "worktree": str(prepared.worktree),
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
        state = self.store.record_action_intent(intent)
        if state.terminal:
            raise LocalDeliveryBlocked(
                f"local child action {intent.key} is already {state.value}"
            )
        self.store.mark_action_running(intent.key)
        try:
            log_root = (
                self.runtime_root
                / "logs"
                / f"p-{parent.issue}-i-{issue_number}-d-{delivery_number}"
            )
            agent = self.agent or DeliveryAgent(
                launcher=CopilotLauncher(
                    log_root, timeout_seconds=self.config.agent_timeout_seconds
                )
            )
            result = agent.run_once(
                record.key, prepared.worktree, manifest.canonical_json
            )
            result_commit = require_single_commit(result)
            self.git.require_single_direct_child(
                prepared.worktree, base_head, result_commit
            )
            validation = self.validator.validate(
                worktree=prepared.worktree,
                base_commit=base_head,
                scope_fingerprint=manifest.fingerprint,
                commands=tuple(
                    ValidationCommand(command)
                    for command in self.config.validation_commands
                ),
            )
            if not validation.passed:
                raise LocalDeliveryBlocked(
                    f"validation failed for local commit {result_commit}"
                )
            validation_identity = hashlib.sha256(
                (
                    f"{validation.head_commit}\0{validation.tree_id}\0"
                    f"{validation.validation_config_fingerprint}"
                ).encode()
            ).hexdigest()
            delivery = CompletionDelivery(
                repository=self.config.repository,
                parent_issue=parent.issue,
                issue_number=issue_number,
                delivery=delivery_number,
                pr_number=None,
                base_ref=parent.feature_branch,
                base_commit=base_head,
                work_head=None,
                scope_fingerprint=manifest.fingerprint,
                job_succeeded=True,
            )
            try:
                self.completion.reconcile(
                    delivery,
                    mode=CompletionMode.LOCAL_COMMIT,
                    resulting_commit=result_commit,
                    validation_identity=validation_identity,
                )
            finally:
                if self.store.completion(
                    record.key, CompletionMode.LOCAL_COMMIT
                ) is not None:
                    self.store.record_action_outcome(
                        intent.key, ActionState.SUCCEEDED
                    )
        except BaseException:
            if (
                self.store.completion(record.key, CompletionMode.LOCAL_COMMIT)
                is None
            ):
                self.workspace.reset(
                    parent_issue=parent.issue,
                    feature_branch=parent.feature_branch,
                    target_head=base_head,
                )
                self.store.record_action_outcome(
                    intent.key, ActionState.FAILED
                )
            raise

    def _parent_delivery(
        self, parent_issue: int, children: tuple[int, ...]
    ) -> ParentDeliveryRecord:
        deliveries = self.store.parent_deliveries(
            self.config.repository, parent_issue
        )
        if deliveries and deliveries[-1].selected_children == children:
            return deliveries[-1]
        return ParentDeliveryRecord(
            self.config.repository,
            parent_issue,
            (deliveries[-1].delivery + 1) if deliveries else 1,
            children,
        )

    def _publish_feature(self, parent_issue: int) -> IntegrationPullRequest:
        parent = _parent(self.config, parent_issue)
        children = _selected_children(
            self.config, parent, self.facts, self.store
        )
        remote_head = self.git.remote_branch_head(
            self.config.repository_path,
            self.config.remote,
            parent.feature_branch,
        )
        if remote_head is None:
            raise LocalDeliveryBlocked(
                f"remote feature branch does not exist: {parent.feature_branch}"
            )
        prepared = self.workspace.prepare(
            repository=self.config.repository_path,
            remote=self.config.remote,
            feature_branch=parent.feature_branch,
            parent_issue=parent.issue,
            expected_remote_head=remote_head,
        )
        local_head = prepared.head
        if local_head != remote_head:
            intent = ActionIntent(
                key=(
                    f"local-publish:{self.config.repository}:"
                    f"{parent.issue}:{local_head}"
                ),
                kind="local_feature_publish",
                target=f"{parent.issue}:{parent.feature_branch}",
                expected_input=json.dumps(
                    {
                        "expected_remote_head": remote_head,
                        "new_head": local_head,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            state = self.store.record_action_intent(intent)
            if not state.terminal:
                self.store.mark_action_running(intent.key)
                self.git.publish_branch(
                    prepared.worktree,
                    self.config.remote,
                    parent.feature_branch,
                    expected_remote_head=remote_head,
                    new_head=local_head,
                )
                self.store.record_action_outcome(
                    intent.key, ActionState.SUCCEEDED
                )

        observed = self.git.remote_branch_head(
            self.config.repository_path,
            self.config.remote,
            parent.feature_branch,
        )
        if observed != local_head:
            raise LocalDeliveryBlocked(
                f"feature branch publication expected {local_head}, observed {observed}"
            )

        delivery = self._parent_delivery(parent.issue, children)
        self.store.record_parent_delivery(delivery)
        identity = IntegrationIdentity(
            self.config.repository, parent.issue, delivery.delivery
        )
        request = IntegrationRequest(
            identity=identity,
            feature_branch=parent.feature_branch,
            feature_head=local_head,
            main_branch=self.config.main_branch,
            title=f"Implement parent issue #{parent.issue}",
            body=(
                f"Implements #{parent.issue} from verified local child commits.\n\n"
                f"Selected children: {', '.join(f'#{child}' for child in children)}"
            ),
        )
        candidates = self.github.list_integration_pull_requests(
            self.config.repository,
            parent.feature_branch,
            self.config.main_branch,
        )
        owned = tuple(
            candidate
            for candidate in candidates
            if integration_pr_owned_by_parent(
                candidate.body,
                repository=self.config.repository,
                parent_issue=parent.issue,
            )
        )
        if len(owned) > 1:
            raise IntegrationBlocked("multiple runner-owned integration PRs found")
        if owned:
            if len(candidates) != 1:
                raise IntegrationBlocked(
                    "runner-owned and external integration PRs both exist"
                )
            candidate = owned[0]
        elif candidates:
            raise IntegrationBlocked("external integration PR observed and not adopted")
        else:
            created = self.github.create_integration_pull_request(request)
            if not isinstance(created, IntegrationPullRequest):
                raise TypeError("GitHub returned an invalid integration pull request")
            candidate = created
        if (
            candidate.head_ref != parent.feature_branch
            or candidate.head_sha != local_head
            or candidate.base_ref != self.config.main_branch
        ):
            raise IntegrationBlocked(
                "integration pull request does not match the published feature head"
            )
        return candidate

    def recover_intent(self, intent: ActionIntent) -> ActionState:
        if intent.kind == "local_feature_publish":
            try:
                parent_text, branch = intent.target.split(":", 1)
                parent_issue = int(parent_text)
                expected = json.loads(intent.expected_input)
                expected_remote_head = str(expected["expected_remote_head"])
                new_head = str(expected["new_head"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise RecoveryBlocked(
                    f"publication intent {intent.key} is malformed"
                ) from error
            worktree = self.workspace._worktree(parent_issue)
            self.git.publish_branch(
                worktree,
                self.config.remote,
                branch,
                expected_remote_head=expected_remote_head,
                new_head=new_head,
            )
            return ActionState.SUCCEEDED
        if intent.kind == "local_child":
            delivery_key = intent.key.removeprefix("local-child:")
            evidence = self.store.completion(
                delivery_key, CompletionMode.LOCAL_COMMIT
            )
            if isinstance(evidence, LocalCommitCompletionEvidence):
                self.completion.reconcile(
                    evidence.delivery,
                    mode=CompletionMode.LOCAL_COMMIT,
                    resulting_commit=evidence.resulting_commit,
                    validation_identity=evidence.validation_identity,
                )
                return ActionState.SUCCEEDED
            try:
                expected = json.loads(intent.expected_input)
                base_head = str(expected["base_head"])
                _, parent_text, _, _ = delivery_key.rsplit(":", 3)
                parent_issue = int(parent_text)
                branch = intent.target
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                raise RecoveryBlocked(
                    f"local child intent {intent.key} is malformed"
                ) from error
            worktree = self.workspace._worktree(parent_issue)
            current_head = self.git.head_commit(worktree)
            if current_head != base_head:
                raise RecoveryBlocked(
                    f"interrupted local child action {intent.key} left commit "
                    f"{current_head} without completion evidence"
                )
            self.workspace.reset(
                parent_issue=parent_issue,
                feature_branch=branch,
                target_head=base_head,
            )
            return ActionState.FAILED
        raise RecoveryBlocked(f"unsupported interrupted action: {intent.kind}")

    def feature_statuses(self) -> tuple[LocalFeatureStatus, ...]:
        statuses: list[LocalFeatureStatus] = []
        for parent in self.config.parents:
            try:
                local_head = self.git.resolve_local_branch(
                    self.config.repository_path, parent.feature_branch
                )
            except Exception:
                local_head = None
            remote_head = self.git.remote_branch_head(
                self.config.repository_path,
                self.config.remote,
                parent.feature_branch,
            )
            unpublished = 0
            if (
                local_head is not None
                and remote_head is not None
                and local_head != remote_head
                and self.git.is_ancestor(
                    self.config.repository_path, remote_head, local_head
                )
            ):
                unpublished = self.git.commit_count(
                    self.config.repository_path, remote_head, local_head
                )
            statuses.append(
                LocalFeatureStatus(
                    parent.issue,
                    parent.feature_branch,
                    local_head,
                    remote_head,
                    unpublished,
                    unpublished > 0,
                )
            )
        return tuple(statuses)
