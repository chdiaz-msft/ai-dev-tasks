from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from local_issue_runner.feedback import (
    FeedbackDecision,
    FeedbackDisposition,
    FeedbackHandler,
    FeedbackItem,
    FeedbackKind,
    FeedbackStore,
    RepairEvidence,
    ThreadState,
)

CREATED_AT = "2026-09-25T12:00:00Z"
UPDATED_AT = "2026-09-25T12:05:00Z"
HEAD_SHA = "a" * 40


def feedback(
    kind: FeedbackKind,
    *,
    item_id: str = "comment-1",
    author: str = "reviewer",
    body: str = "Please handle this feedback.",
    updated_at: str = CREATED_AT,
    thread_id: str | None = None,
    thread_state: ThreadState | None = None,
) -> FeedbackItem:
    return FeedbackItem(
        kind=kind,
        item_id=item_id,
        author_login=author,
        body=body,
        created_at=CREATED_AT,
        updated_at=updated_at,
        thread_id=thread_id,
        thread_state=thread_state,
    )


@dataclass
class RecordingReplies:
    calls: list[tuple[int, str, str]] = field(default_factory=list)
    resolutions: list[str] = field(default_factory=list)

    def reply(self, pr_number: int, item: FeedbackItem, body: str) -> str:
        self.calls.append((pr_number, item.item_id, body))
        return f"runner-reply-{len(self.calls)}"

    def resolve_thread(self, thread_id: str) -> None:
        self.resolutions.append(thread_id)


@dataclass
class RecordingRepairs:
    calls: list[tuple[int, str]] = field(default_factory=list)

    def repair(self, pr_number: int, item: FeedbackItem) -> RepairEvidence:
        self.calls.append((pr_number, item.item_id))
        return RepairEvidence(
            head_sha=HEAD_SHA,
            validation_summary="Targeted feedback tests passed.",
        )


def handler(
    tmp_path: Path,
) -> tuple[FeedbackHandler, FeedbackStore, RecordingReplies, RecordingRepairs]:
    store = FeedbackStore(tmp_path / "runner.db")
    replies = RecordingReplies()
    repairs = RecordingRepairs()
    return (
        FeedbackHandler(
            store=store,
            replies=replies,
            repairs=repairs,
            runner_login="shared-account",
        ),
        store,
        replies,
        repairs,
    )


def informational(_: FeedbackItem) -> FeedbackDecision:
    return FeedbackDecision(
        disposition=FeedbackDisposition.INFORMATIONAL,
        reason="Acknowledged; no implementation change was requested.",
    )


@pytest.mark.parametrize(
    "item",
    [
        feedback(
            FeedbackKind.REVIEW_THREAD_COMMENT,
            thread_id="thread-1",
            thread_state=ThreadState.OPEN,
        ),
        feedback(FeedbackKind.ISSUE_COMMENT),
        feedback(FeedbackKind.REVIEW_BODY, body="Please revisit the error handling."),
    ],
)
def test_all_external_pr_feedback_surfaces_are_handled(
    tmp_path: Path, item: FeedbackItem
) -> None:
    subject, store, replies, _ = handler(tmp_path)

    result = subject.reconcile(17, (item,), decide=informational)

    assert result.handled_feedback == (item.identity,)
    assert result.quiet_period_restarted is True
    assert replies.calls[0][:2] == (17, item.item_id)
    assert store.disposition(item.identity) is FeedbackDisposition.INFORMATIONAL


def test_empty_review_body_is_not_feedback(tmp_path: Path) -> None:
    subject, _, replies, _ = handler(tmp_path)
    item = feedback(FeedbackKind.REVIEW_BODY, body=" \n ")

    result = subject.reconcile(17, (item,), decide=informational)

    assert result.handled_feedback == ()
    assert result.quiet_period_restarted is False
    assert replies.calls == []


def test_material_edit_is_a_new_revision_but_unchanged_feedback_is_not_replied_twice(
    tmp_path: Path,
) -> None:
    subject, _, replies, _ = handler(tmp_path)
    original = feedback(FeedbackKind.ISSUE_COMMENT)
    edited = feedback(
        FeedbackKind.ISSUE_COMMENT,
        body="Updated feedback with a materially different request.",
        updated_at=UPDATED_AT,
    )

    first = subject.reconcile(17, (original,), decide=informational)
    unchanged = subject.reconcile(17, (original,), decide=informational)
    changed = subject.reconcile(17, (edited,), decide=informational)

    assert first.handled_feedback == (original.identity,)
    assert unchanged.handled_feedback == ()
    assert unchanged.quiet_period_restarted is False
    assert changed.handled_feedback == (edited.identity,)
    assert changed.quiet_period_restarted is True
    assert len(replies.calls) == 2


def test_explicitly_reopened_thread_is_handled_as_new_feedback(
    tmp_path: Path,
) -> None:
    subject, _, replies, _ = handler(tmp_path)
    resolved = feedback(
        FeedbackKind.REVIEW_THREAD_COMMENT,
        thread_id="thread-1",
        thread_state=ThreadState.RESOLVED,
    )
    reopened = feedback(
        FeedbackKind.REVIEW_THREAD_COMMENT,
        thread_id="thread-1",
        thread_state=ThreadState.OPEN,
    )

    subject.reconcile(17, (resolved,), decide=informational)
    result = subject.reconcile(17, (reopened,), decide=informational)

    assert result.handled_feedback == (reopened.identity,)
    assert result.quiet_period_restarted is True
    assert len(replies.calls) == 2


def test_same_account_human_feedback_is_handled_but_recorded_runner_reply_is_excluded(
    tmp_path: Path,
) -> None:
    subject, store, replies, _ = handler(tmp_path)
    human = feedback(
        FeedbackKind.ISSUE_COMMENT,
        item_id="manual-comment",
        author="shared-account",
    )
    runner_reply = feedback(
        FeedbackKind.ISSUE_COMMENT,
        item_id="known-runner-output",
        author="shared-account",
    )
    store.record_runner_output(runner_reply.identity)

    result = subject.reconcile(
        17, (human, runner_reply), decide=informational
    )

    assert result.handled_feedback == (human.identity,)
    assert [call[1] for call in replies.calls] == ["manual-comment"]
    assert store.disposition(runner_reply.identity) is None


def test_worthwhile_feedback_runs_repair_and_replies_with_validation_evidence(
    tmp_path: Path,
) -> None:
    subject, store, replies, repairs = handler(tmp_path)
    item = feedback(
        FeedbackKind.REVIEW_THREAD_COMMENT,
        thread_id="thread-1",
        thread_state=ThreadState.OPEN,
    )

    result = subject.reconcile(
        17,
        (item,),
        decide=lambda _: FeedbackDecision(
            disposition=FeedbackDisposition.IMPLEMENTED,
            reason="The requested guard prevents an invalid state.",
        ),
    )

    assert result.handled_feedback == (item.identity,)
    assert repairs.calls == [(17, item.item_id)]
    assert HEAD_SHA in replies.calls[0][2]
    assert "Targeted feedback tests passed." in replies.calls[0][2]
    assert replies.resolutions == ["thread-1"]
    assert store.disposition(item.identity) is FeedbackDisposition.IMPLEMENTED


@pytest.mark.parametrize(
    ("disposition", "reason"),
    [
        (
            FeedbackDisposition.NO_CHANGE,
            "The suggested behavior is outside the child issue scope.",
        ),
        (
            FeedbackDisposition.INFORMATIONAL,
            "Acknowledged; no implementation change was requested.",
        ),
    ],
)
def test_no_change_and_informational_feedback_get_reasoned_recorded_replies(
    tmp_path: Path,
    disposition: FeedbackDisposition,
    reason: str,
) -> None:
    subject, store, replies, repairs = handler(tmp_path)
    item = feedback(FeedbackKind.ISSUE_COMMENT)

    def decide(_: FeedbackItem) -> FeedbackDecision:
        return FeedbackDecision(disposition=disposition, reason=reason)

    subject.reconcile(17, (item,), decide=decide)

    assert repairs.calls == []
    assert reason in replies.calls[0][2]
    assert replies.resolutions == []
    assert store.disposition(item.identity) is disposition
