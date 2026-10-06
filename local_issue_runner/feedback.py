from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from typing import Protocol

from local_issue_runner.db import FeedbackStore
from local_issue_runner.models import (
    FeedbackDecision,
    FeedbackDisposition,
    FeedbackItem,
    FeedbackKind,
    FeedbackReconcileResult,
    RepairEvidence,
    ThreadState,
)

__all__ = [
    "FeedbackDecision",
    "FeedbackDisposition",
    "FeedbackHandler",
    "FeedbackItem",
    "FeedbackKind",
    "FeedbackStore",
    "RepairEvidence",
    "ThreadState",
    "canonical_feedback_fingerprint",
]


def _body(item: FeedbackItem) -> str:
    return item.body.replace("\r\n", "\n").replace("\r", "\n").strip()


def _body_hash(item: FeedbackItem) -> str:
    return hashlib.sha256(_body(item).encode()).hexdigest()


def _revision_fingerprint(item: FeedbackItem) -> str:
    material = (
        item.kind.value,
        item.item_id,
        item.author_login,
        _body_hash(item),
        item.thread_id,
        None if item.thread_state is None else item.thread_state.value,
    )
    return hashlib.sha256(
        json.dumps(material, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def canonical_feedback_fingerprint(items: Iterable[FeedbackItem]) -> str:
    facts = sorted(
        (
            item.kind.value,
            item.item_id,
            item.author_login,
            item.created_at,
            item.updated_at,
            item.thread_id,
            None if item.thread_state is None else item.thread_state.value,
            _body_hash(item),
        )
        for item in items
    )
    return hashlib.sha256(
        json.dumps(facts, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


def _repair_evidence(
    decision: FeedbackDecision,
    *,
    repairs: RepairPort,
    pr_number: int,
    item: FeedbackItem,
) -> RepairEvidence | None:
    if decision.disposition is not FeedbackDisposition.IMPLEMENTED:
        return None
    return repairs.repair(pr_number, item)


def _reply_body(
    decision: FeedbackDecision,
    evidence: RepairEvidence | None,
) -> str:
    if evidence is None:
        return decision.reason
    return (
        f"{decision.reason}\n\n"
        f"Implemented in `{evidence.head_sha}`. "
        f"Validation: {evidence.validation_summary}"
    )


def _should_resolve_thread(
    item: FeedbackItem,
    disposition: FeedbackDisposition,
) -> bool:
    return (
        item.thread_id is not None
        and item.thread_state is ThreadState.OPEN
        and disposition
        in {
            FeedbackDisposition.IMPLEMENTED,
            FeedbackDisposition.NO_CHANGE,
            FeedbackDisposition.INFORMATIONAL,
        }
    )


def _should_restart_quiet_period(
    item: FeedbackItem,
    prior_thread_state: ThreadState | None,
) -> bool:
    return not (
        item.thread_state is ThreadState.RESOLVED
        and prior_thread_state is ThreadState.RESOLVED
    )


class ReplyPort(Protocol):
    def reply(self, pr_number: int, item: FeedbackItem, body: str) -> str: ...

    def resolve_thread(self, thread_id: str) -> None: ...


class RepairPort(Protocol):
    def repair(self, pr_number: int, item: FeedbackItem) -> RepairEvidence: ...


class FeedbackHandler:
    def __init__(
        self,
        *,
        store: FeedbackStore,
        replies: ReplyPort,
        repairs: RepairPort,
        runner_login: str,
    ) -> None:
        self.store = store
        self.replies = replies
        self.repairs = repairs
        self.runner_login = runner_login

    def reconcile(
        self,
        pr_number: int,
        items: Iterable[FeedbackItem],
        *,
        decide: Callable[[FeedbackItem], FeedbackDecision],
    ) -> FeedbackReconcileResult:
        handled: list[str] = []
        restarted = False
        for item in sorted(items, key=lambda value: (value.kind.value, value.item_id)):
            if not _body(item) or self.store.is_runner_output(item.identity):
                continue
            prior_thread_state = (
                self.store.latest_thread_state(item.thread_id)
                if item.thread_id is not None
                else None
            )
            revision = _revision_fingerprint(item)
            if not self.store.begin_revision(item, revision, _body_hash(item)):
                continue

            decision = decide(item)
            evidence = _repair_evidence(
                decision,
                repairs=self.repairs,
                pr_number=pr_number,
                item=item,
            )
            reply_body = self.store.record_reply_intent(
                revision, pr_number, _reply_body(decision, evidence)
            )
            output_identity = self.replies.reply(pr_number, item, reply_body)
            self.store.complete_reply(revision, output_identity)

            if _should_resolve_thread(item, decision.disposition):
                assert item.thread_id is not None
                self.store.record_resolution_intent(revision, item.thread_id)
                self.replies.resolve_thread(item.thread_id)
                self.store.complete_resolution(revision)

            self.store.complete_revision(
                revision, decision.disposition, decision.reason
            )
            if _should_restart_quiet_period(item, prior_thread_state):
                self.store.restart_quiet_period(pr_number, revision)
                restarted = True
            handled.append(item.identity)
        return FeedbackReconcileResult(tuple(handled), restarted)
