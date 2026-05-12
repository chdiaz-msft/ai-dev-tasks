"""Tests for orchestrator.escalation_policy module."""

from datetime import datetime, timezone, timedelta

from orchestrator.escalation_policy import (
    MAX_FIX_ATTEMPTS,
    SLA_SECONDS,
    apply_escalations,
    _check_fix_attempt_limit,
    _check_recurrence,
    _check_sla_violation,
)
from orchestrator.models import Issue


def _make_issue(
    issue_id: str = "test-001",
    reviewer: str = "correctness",
    severity: str = "medium",
    status: str = "open",
    found_in_round: int = 1,
    fix_attempts: int = 0,
    original_severity: str | None = None,
    escalated: bool = False,
) -> Issue:
    """Helper to create an Issue with sensible defaults."""
    return Issue(
        issue_id=issue_id,
        reviewer=reviewer,
        severity=severity,
        issue="Test issue",
        suggested_fix="Fix it",
        file="src/main.py",
        line=42,
        found_in_round=found_in_round,
        status=status,
        fix_attempts=fix_attempts,
        original_severity=original_severity,
        escalated=escalated,
    )


class TestCheckFixAttemptLimit:
    """Tests for _check_fix_attempt_limit."""

    def test_under_limit(self) -> None:
        issue = _make_issue(fix_attempts=1)
        assert _check_fix_attempt_limit(issue) is False

    def test_at_limit(self) -> None:
        issue = _make_issue(fix_attempts=MAX_FIX_ATTEMPTS)
        assert _check_fix_attempt_limit(issue) is True

    def test_over_limit(self) -> None:
        issue = _make_issue(fix_attempts=MAX_FIX_ATTEMPTS + 1)
        assert _check_fix_attempt_limit(issue) is True


class TestCheckSlaViolation:
    """Tests for _check_sla_violation."""

    def test_within_sla(self) -> None:
        issue = _make_issue(found_in_round=1)
        now = datetime(2025, 1, 1, 0, 10, 0, tzinfo=timezone.utc)
        timestamps = {1: "2025-01-01T00:00:00+00:00"}
        assert _check_sla_violation(issue, timestamps, now) is False

    def test_sla_exceeded(self) -> None:
        issue = _make_issue(found_in_round=1)
        now = datetime(2025, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
        timestamps = {1: "2025-01-01T00:00:00+00:00"}
        assert _check_sla_violation(issue, timestamps, now) is True

    def test_missing_timestamp(self) -> None:
        issue = _make_issue(found_in_round=1)
        now = datetime.now(timezone.utc)
        assert _check_sla_violation(issue, {}, now) is False


class TestCheckRecurrence:
    """Tests for _check_recurrence."""

    def test_new_issue_not_recurring(self) -> None:
        issue = _make_issue(fix_attempts=0)
        assert _check_recurrence(issue, current_round=2) is False

    def test_recurring_issue(self) -> None:
        issue = _make_issue(fix_attempts=2, status="open")
        assert _check_recurrence(issue, current_round=3) is True

    def test_resolved_not_recurring(self) -> None:
        issue = _make_issue(fix_attempts=2, status="resolved")
        assert _check_recurrence(issue, current_round=3) is False


class TestApplyEscalations:
    """Tests for apply_escalations."""

    def test_no_escalation_for_new_issues(self) -> None:
        issues = {"a": _make_issue(fix_attempts=0)}
        events = apply_escalations(issues, current_round=1, round_timestamps={})
        assert len(events) == 0

    def test_escalation_on_fix_attempt_limit(self) -> None:
        issues = {
            "a": _make_issue(
                issue_id="a",
                severity="medium",
                fix_attempts=MAX_FIX_ATTEMPTS,
            ),
        }
        events = apply_escalations(issues, current_round=3, round_timestamps={})
        assert len(events) == 1
        assert events[0].from_severity == "medium"
        assert events[0].to_severity == "high"
        assert issues["a"].severity == "high"
        assert issues["a"].original_severity == "medium"
        assert issues["a"].escalated is True

    def test_no_escalation_beyond_critical(self) -> None:
        issues = {
            "a": _make_issue(
                issue_id="a",
                severity="critical",
                fix_attempts=MAX_FIX_ATTEMPTS + 1,
            ),
        }
        events = apply_escalations(issues, current_round=5, round_timestamps={})
        assert len(events) == 0  # Already critical, skip

    def test_original_severity_preserved_on_first_escalation(self) -> None:
        issues = {
            "a": _make_issue(
                issue_id="a",
                severity="low",
                fix_attempts=MAX_FIX_ATTEMPTS,
            ),
        }
        apply_escalations(issues, current_round=2, round_timestamps={})
        assert issues["a"].original_severity == "low"
        assert issues["a"].severity == "medium"

    def test_original_severity_not_overwritten_on_second_escalation(self) -> None:
        issues = {
            "a": _make_issue(
                issue_id="a",
                severity="medium",
                fix_attempts=MAX_FIX_ATTEMPTS,
                original_severity="low",
                escalated=True,
            ),
        }
        apply_escalations(issues, current_round=4, round_timestamps={})
        assert issues["a"].original_severity == "low"  # Still the original
        assert issues["a"].severity == "high"  # Bumped again

    def test_config_overrides(self) -> None:
        issues = {
            "a": _make_issue(
                issue_id="a",
                severity="medium",
                fix_attempts=1,  # Under default limit of 3
            ),
        }
        config = {"max_fix_attempts": 1}
        events = apply_escalations(
            issues, current_round=2, round_timestamps={}, escalation_config=config
        )
        assert len(events) == 1  # Escalated because config lowered threshold

    def test_resolved_issues_not_escalated(self) -> None:
        issues = {
            "a": _make_issue(
                issue_id="a",
                severity="medium",
                status="resolved",
                fix_attempts=10,
            ),
        }
        events = apply_escalations(issues, current_round=5, round_timestamps={})
        assert len(events) == 0

    def test_sla_based_escalation(self) -> None:
        issues = {
            "a": _make_issue(
                issue_id="a",
                severity="low",
                found_in_round=1,
                fix_attempts=0,
            ),
        }
        # Timestamp from 2 hours ago
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        round_timestamps = {1: old_ts}
        events = apply_escalations(issues, current_round=3, round_timestamps=round_timestamps)
        assert len(events) == 1
        assert "SLA" in events[0].reason

    def test_multiple_issues_escalated(self) -> None:
        issues = {
            "a": _make_issue(issue_id="a", severity="medium", fix_attempts=MAX_FIX_ATTEMPTS),
            "b": _make_issue(issue_id="b", severity="low", fix_attempts=MAX_FIX_ATTEMPTS),
            "c": _make_issue(issue_id="c", severity="high", fix_attempts=0),  # Should NOT escalate
        }
        events = apply_escalations(issues, current_round=4, round_timestamps={})
        escalated_ids = {e.issue_id for e in events}
        assert "a" in escalated_ids
        assert "b" in escalated_ids
        assert "c" not in escalated_ids
