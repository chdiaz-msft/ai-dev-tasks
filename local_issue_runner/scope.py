from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict


class FeatureScope(TypedDict):
    branch: str
    parent_issue: int


class IssueScope(TypedDict):
    body: str
    number: int
    title: str


class ScopeContent(TypedDict):
    acceptance_criteria: list[str]
    decisions: list[dict[str, object]]
    delivery: int
    feature: FeatureScope
    issue: IssueScope
    linked_specs: list[dict[str, object]]
    prerequisites: list[dict[str, object]]
    repository: str


@dataclass(frozen=True, slots=True)
class ChildScopeInput:
    repository: str
    parent_issue: int
    issue_number: int
    delivery: int
    title: str
    body: str
    feature_branch: str
    prerequisites: tuple[Mapping[str, object], ...] = ()
    acceptance_criteria: tuple[str, ...] = ()
    linked_specs: tuple[Mapping[str, object], ...] = ()
    decisions: tuple[Mapping[str, object], ...] = ()

    def __post_init__(self) -> None:
        if min(self.parent_issue, self.issue_number, self.delivery) <= 0:
            raise ValueError("scope issue and delivery values must be positive")
        if not self.repository or not self.title or not self.feature_branch:
            raise ValueError("scope repository, title, and feature branch are required")


@dataclass(frozen=True, slots=True)
class ScopeManifest:
    content: ScopeContent
    canonical_json: bytes
    fingerprint: str

    def persist(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_bytes(self.canonical_json)
        temporary.replace(path)


@dataclass(frozen=True, slots=True)
class LinkedSpecRevision:
    path: str
    revision: str

    def __post_init__(self) -> None:
        if not self.path or not self.revision:
            raise ValueError("linked spec path and revision are required")


@dataclass(frozen=True, slots=True)
class ChildRequirement:
    issue_number: int
    title: str
    body: str
    linked_specs: tuple[LinkedSpecRevision, ...] = ()

    def __post_init__(self) -> None:
        if self.issue_number <= 0:
            raise ValueError("requirement issue number must be positive")
        if not self.title:
            raise ValueError("requirement title is required")

    @property
    def canonical_json(self) -> bytes:
        return json.dumps(
            {
                "body": self.body,
                "issue_number": self.issue_number,
                "linked_specs": [
                    {"path": item.path, "revision": item.revision}
                    for item in self.linked_specs
                ],
                "title": self.title,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.canonical_json).hexdigest()


@dataclass(frozen=True, slots=True)
class DeliveryState:
    issue_number: int
    delivery: int
    scope: ChildRequirement
    branch: str
    worktree: Path
    pr_number: int | None
    merged: bool
    issue_state: str
    prerequisites: tuple[int, ...] = ()
    job_running: bool = False

    @property
    def scope_fingerprint(self) -> str:
        return self.scope.fingerprint


@dataclass(frozen=True, slots=True)
class IntegrationState:
    delivery: int
    selected_children: tuple[int, ...]
    ready: bool = False
    delivered: bool = False


@dataclass(frozen=True, slots=True)
class FeatureScopeState:
    repository: str
    parent_issue: int
    feature_branch: str
    requirements: tuple[ChildRequirement, ...]
    deliveries: tuple[DeliveryState, ...]
    integration: IntegrationState
    parent_issue_state: str = "OPEN"


def latest_deliveries(
    deliveries: tuple[DeliveryState, ...],
) -> dict[int, DeliveryState]:
    latest: dict[int, DeliveryState] = {}
    for delivery in deliveries:
        current = latest.get(delivery.issue_number)
        if current is None or delivery.delivery > current.delivery:
            latest[delivery.issue_number] = delivery
    return latest


def delivery_key(feature: FeatureScopeState, delivery: DeliveryState) -> str:
    return (
        f"{feature.repository}:{feature.parent_issue}:"
        f"{delivery.issue_number}:{delivery.delivery}"
    )


def allocate_delivery(
    feature: FeatureScopeState,
    requirement: ChildRequirement,
    delivery_number: int,
) -> DeliveryState:
    slug = (
        f"p-{feature.parent_issue}-i-{requirement.issue_number}"
        f"-d-{delivery_number}"
    )
    return DeliveryState(
        issue_number=requirement.issue_number,
        delivery=delivery_number,
        scope=requirement,
        branch=(
            f"runner/p-{feature.parent_issue}/i-{requirement.issue_number}"
            f"/d-{delivery_number}"
        ),
        worktree=Path("runtime/worktrees") / slug,
        pr_number=None,
        merged=False,
        issue_state="OPEN",
        job_running=False,
    )


def build_child_scope_manifest(source: ChildScopeInput) -> ScopeManifest:
    content: ScopeContent = {
        "acceptance_criteria": list(source.acceptance_criteria),
        "decisions": [dict(value) for value in source.decisions],
        "delivery": source.delivery,
        "feature": {
            "branch": source.feature_branch,
            "parent_issue": source.parent_issue,
        },
        "issue": {
            "body": source.body,
            "number": source.issue_number,
            "title": source.title,
        },
        "linked_specs": [dict(value) for value in source.linked_specs],
        "prerequisites": [dict(value) for value in source.prerequisites],
        "repository": source.repository,
    }
    canonical = json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return ScopeManifest(
        content,
        canonical,
        hashlib.sha256(canonical).hexdigest(),
    )
