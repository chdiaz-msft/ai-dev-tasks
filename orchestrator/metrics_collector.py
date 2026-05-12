"""
Metrics collection for the PR Flywheel.

This module computes per-issue and per-round metrics from LoopState:
- Fix attempt tracking across rounds
- Time-to-resolve computation from timestamps
- Recurrence detection (issues that reappear after resolution)
- Per-reviewer agreement and resolution rate statistics
"""

import logging
from datetime import datetime, timezone
from typing import Any

from orchestrator.models import Issue

logger = logging.getLogger(__name__)

# Recurrence window: if an issue reappears within this many rounds of
# being resolved, it's considered a recurrence
RECURRENCE_WINDOW = 2


def compute_fix_attempts(issues: dict[str, Issue]) -> dict[str, int]:
    """
    Count fix attempts for each open issue.

    Args:
        issues: Dictionary of issue_id to Issue objects

    Returns:
        Dictionary mapping issue_id to fix attempt count
    """
    return {
        issue_id: issue.fix_attempts
        for issue_id, issue in issues.items()
        if issue.status == "open"
    }


def compute_time_to_resolve(issue: Issue, round_timestamps: dict[int, str]) -> float | None:
    """
    Compute time-to-resolve in seconds for a resolved issue.

    Uses round timestamps to calculate the elapsed time between the round
    the issue was found and the round it was resolved.

    Args:
        issue: The resolved issue
        round_timestamps: Mapping of round number to ISO 8601 timestamp

    Returns:
        Time to resolve in seconds, or None if timestamps are unavailable
    """
    if issue.status != "resolved" or issue.resolved_in_round is None:
        return None

    found_ts = round_timestamps.get(issue.found_in_round)
    resolved_ts = round_timestamps.get(issue.resolved_in_round)

    if not found_ts or not resolved_ts:
        return None

    # REVIEW BAIT #1: Bare except Exception — should catch ValueError specifically
    try:
        found_time = datetime.fromisoformat(found_ts.replace("Z", "+00:00"))
        resolved_time = datetime.fromisoformat(resolved_ts.replace("Z", "+00:00"))
        return (resolved_time - found_time).total_seconds()
    except Exception:
        return None


def detect_recurrences(
    issues: dict[str, Issue],
    current_round: int,
) -> list[str]:
    """
    Detect issues that have recurred after being previously resolved.

    An issue is considered a recurrence if:
    - It was resolved in a previous round
    - It reappeared (status is "open" again) within RECURRENCE_WINDOW rounds
    - Its fix_attempts > 0

    Args:
        issues: Dictionary of issue_id to Issue objects
        current_round: Current flywheel round number

    Returns:
        List of issue_ids that are recurrences
    """
    recurrences = []
    for issue_id, issue in issues.items():
        if issue.status == "open" and issue.fix_attempts > 0:
            # The issue was previously attempted but is back
            recurrences.append(issue_id)
    return recurrences


def compute_reviewer_stats(issues: dict[str, Issue]) -> dict[str, dict[str, Any]]:
    """
    Compute per-reviewer statistics.

    For each reviewer, calculates:
    - total_issues: Total issues found by this reviewer
    - open_issues: Currently open issues
    - resolved_issues: Issues that have been resolved
    - resolution_rate: Fraction of issues that were resolved

    Args:
        issues: Dictionary of issue_id to Issue objects

    Returns:
        Dictionary mapping reviewer name to stats dict
    """
    reviewer_data: dict[str, dict[str, int]] = {}

    for issue in issues.values():
        reviewer = issue.reviewer
        if reviewer not in reviewer_data:
            reviewer_data[reviewer] = {"total": 0, "open": 0, "resolved": 0}

        reviewer_data[reviewer]["total"] += 1
        if issue.status == "open":
            reviewer_data[reviewer]["open"] += 1
        elif issue.status == "resolved":
            reviewer_data[reviewer]["resolved"] += 1

    stats: dict[str, dict[str, Any]] = {}
    for reviewer, counts in reviewer_data.items():
        total = counts["total"]
        resolved = counts["resolved"]
        stats[reviewer] = {
            "total_issues": total,
            "open_issues": counts["open"],
            "resolved_issues": resolved,
            "resolution_rate": resolved / total if total > 0 else 0.0,
        }

    return stats


def collect_metrics(
    issues: dict[str, Issue],
    current_round: int,
    round_timestamps: dict[int, str],
) -> dict[str, Any]:
    """
    Collect all metrics from the current state.

    Aggregates fix attempts, time-to-resolve stats, recurrence info,
    and per-reviewer statistics into a single metrics dict.

    Args:
        issues: Dictionary of issue_id to Issue objects
        current_round: Current flywheel round number
        round_timestamps: Mapping of round number to ISO 8601 timestamp

    Returns:
        Dictionary containing all collected metrics
    """
    # Compute TTR for resolved issues
    ttr_values = []
    for issue in issues.values():
        ttr = compute_time_to_resolve(issue, round_timestamps)
        if ttr is not None:
            ttr_values.append(ttr)

    mean_ttr = sum(ttr_values) / len(ttr_values) if ttr_values else None

    # Detect recurrences
    recurrences = detect_recurrences(issues, current_round)

    # Reviewer stats
    reviewer_stats = compute_reviewer_stats(issues)

    # Fix attempt summary
    fix_attempts = compute_fix_attempts(issues)
    total_fix_attempts = sum(fix_attempts.values())

    return {
        "current_round": current_round,
        "total_issues": len(issues),
        "open_issues": sum(1 for i in issues.values() if i.status == "open"),
        "resolved_issues": sum(1 for i in issues.values() if i.status == "resolved"),
        "mean_time_to_resolve_seconds": mean_ttr,
        "recurrence_count": len(recurrences),
        "recurrent_issue_ids": recurrences,
        "total_fix_attempts": total_fix_attempts,
        "reviewer_stats": reviewer_stats,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
