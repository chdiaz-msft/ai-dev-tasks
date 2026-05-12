"""Tests for orchestrator.metrics_collector module."""

from orchestrator.metrics_collector import (
    collect_metrics,
    compute_fix_attempts,
    compute_reviewer_stats,
    compute_time_to_resolve,
    detect_recurrences,
)
from orchestrator.models import Issue


def _make_issue(
    issue_id: str = "test-001",
    reviewer: str = "correctness",
    severity: str = "high",
    status: str = "open",
    found_in_round: int = 1,
    resolved_in_round: int | None = None,
    fix_attempts: int = 0,
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
        resolved_in_round=resolved_in_round,
        fix_attempts=fix_attempts,
    )


class TestComputeFixAttempts:
    """Tests for compute_fix_attempts."""

    def test_empty_issues(self) -> None:
        result = compute_fix_attempts({})
        assert result == {}

    def test_only_open_issues_returned(self) -> None:
        issues = {
            "open-1": _make_issue(issue_id="open-1", status="open", fix_attempts=2),
            "resolved-1": _make_issue(issue_id="resolved-1", status="resolved", fix_attempts=1),
        }
        result = compute_fix_attempts(issues)
        assert "open-1" in result
        assert "resolved-1" not in result

    def test_fix_attempt_count_accurate(self) -> None:
        issues = {
            "a": _make_issue(issue_id="a", fix_attempts=3),
            "b": _make_issue(issue_id="b", fix_attempts=0),
        }
        result = compute_fix_attempts(issues)
        assert result["a"] == 3
        assert result["b"] == 0


class TestComputeTimeToResolve:
    """Tests for compute_time_to_resolve."""

    def test_resolved_issue_with_timestamps(self) -> None:
        issue = _make_issue(
            status="resolved",
            found_in_round=1,
            resolved_in_round=3,
        )
        round_timestamps = {
            1: "2025-01-01T00:00:00+00:00",
            3: "2025-01-01T00:10:00+00:00",
        }
        result = compute_time_to_resolve(issue, round_timestamps)
        assert result == 600.0  # 10 minutes

    def test_open_issue_returns_none(self) -> None:
        issue = _make_issue(status="open")
        result = compute_time_to_resolve(issue, {})
        assert result is None

    def test_missing_timestamps_returns_none(self) -> None:
        issue = _make_issue(status="resolved", resolved_in_round=2)
        result = compute_time_to_resolve(issue, {})
        assert result is None

    def test_same_round_resolution(self) -> None:
        issue = _make_issue(
            status="resolved",
            found_in_round=1,
            resolved_in_round=1,
        )
        round_timestamps = {1: "2025-01-01T00:00:00+00:00"}
        result = compute_time_to_resolve(issue, round_timestamps)
        assert result == 0.0


class TestDetectRecurrences:
    """Tests for detect_recurrences."""

    def test_no_recurrences(self) -> None:
        issues = {"a": _make_issue(fix_attempts=0)}
        result = detect_recurrences(issues, current_round=2)
        assert result == []

    def test_recurring_issue_detected(self) -> None:
        issues = {
            "a": _make_issue(issue_id="a", fix_attempts=2, status="open"),
        }
        result = detect_recurrences(issues, current_round=3)
        assert "a" in result

    def test_resolved_issue_not_recurrence(self) -> None:
        issues = {
            "a": _make_issue(issue_id="a", fix_attempts=2, status="resolved"),
        }
        result = detect_recurrences(issues, current_round=3)
        assert result == []


class TestComputeReviewerStats:
    """Tests for compute_reviewer_stats."""

    def test_single_reviewer(self) -> None:
        issues = {
            "a": _make_issue(reviewer="security", status="open"),
            "b": _make_issue(reviewer="security", status="resolved"),
        }
        stats = compute_reviewer_stats(issues)
        assert "security" in stats
        assert stats["security"]["total_issues"] == 2
        assert stats["security"]["open_issues"] == 1
        assert stats["security"]["resolved_issues"] == 1
        assert stats["security"]["resolution_rate"] == 0.5

    def test_multiple_reviewers(self) -> None:
        issues = {
            "a": _make_issue(reviewer="correctness"),
            "b": _make_issue(reviewer="security"),
        }
        stats = compute_reviewer_stats(issues)
        assert len(stats) == 2

    def test_empty_issues(self) -> None:
        stats = compute_reviewer_stats({})
        assert stats == {}


class TestCollectMetrics:
    """Tests for collect_metrics."""

    def test_basic_collection(self) -> None:
        issues = {
            "a": _make_issue(issue_id="a", status="open"),
            "b": _make_issue(issue_id="b", status="resolved", resolved_in_round=2),
        }
        metrics = collect_metrics(issues, current_round=3, round_timestamps={})
        assert metrics["total_issues"] == 2
        assert metrics["open_issues"] == 1
        assert metrics["resolved_issues"] == 1
        assert "timestamp" in metrics

    def test_mean_ttr_computed(self) -> None:
        issues = {
            "a": _make_issue(
                issue_id="a",
                status="resolved",
                found_in_round=1,
                resolved_in_round=2,
            ),
        }
        round_timestamps = {
            1: "2025-01-01T00:00:00+00:00",
            2: "2025-01-01T00:05:00+00:00",
        }
        metrics = collect_metrics(issues, current_round=2, round_timestamps=round_timestamps)
        assert metrics["mean_time_to_resolve_seconds"] == 300.0

    def test_no_resolved_issues_mean_ttr_none(self) -> None:
        issues = {"a": _make_issue(status="open")}
        metrics = collect_metrics(issues, current_round=1, round_timestamps={})
        assert metrics["mean_time_to_resolve_seconds"] is None
