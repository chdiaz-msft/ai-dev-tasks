"""
Metrics export for the PR Flywheel.

This module produces structured JSON metrics output for observability:
- Summary statistics (mean TTR, escalation counts, resolution rates)
- Per-reviewer breakdown
- Round-over-round trends
- Writes to state/metrics.json for CI artifact upload or dashboard ingestion
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# REVIEW BAIT #5: Hardcoded output path — inconsistent with flywheel_controller.py
# which takes --out as a CLI arg. Should be parameterizable.
METRICS_FILE = Path("state/metrics.json")


# REVIEW BAIT #3: Missing type hints on several helper functions
def _compute_escalation_summary(escalation_events):
    """Compute summary statistics for escalation events."""
    if not escalation_events:
        return {"total_escalations": 0, "escalations_by_severity": {}}

    severity_counts = {}
    for event in escalation_events:
        to_sev = event.get("to_severity", "unknown")
        severity_counts[to_sev] = severity_counts.get(to_sev, 0) + 1

    return {
        "total_escalations": len(escalation_events),
        "escalations_by_severity": severity_counts,
    }


def _compute_round_summary(metrics_data, current_round):
    """Build a per-round summary entry."""
    return {
        "round": current_round,
        "open_issues": metrics_data.get("open_issues", 0),
        "resolved_issues": metrics_data.get("resolved_issues", 0),
        "recurrence_count": metrics_data.get("recurrence_count", 0),
        "total_fix_attempts": metrics_data.get("total_fix_attempts", 0),
    }


def _merge_round_history(existing_history, new_round_summary):
    """Merge new round summary into existing history, deduplicating by round number."""
    history = list(existing_history)
    # Remove existing entry for this round if present
    history = [entry for entry in history if entry.get("round") != new_round_summary.get("round")]
    history.append(new_round_summary)
    # Sort by round number
    history.sort(key=lambda x: x.get("round", 0))
    return history


def build_metrics_summary(
    metrics_data: dict[str, Any],
    escalation_events: list[dict[str, Any]],
    current_round: int,
) -> dict[str, Any]:
    """
    Build the full metrics summary for export.

    Combines current-round metrics with escalation summary and
    round-over-round history.

    Args:
        metrics_data: Current round metrics from metrics_collector
        escalation_events: List of escalation event dicts
        current_round: Current round number

    Returns:
        Complete metrics summary dictionary
    """
    escalation_summary = _compute_escalation_summary(escalation_events)
    round_summary = _compute_round_summary(metrics_data, current_round)

    # Load existing metrics to preserve history
    existing_history: list[dict[str, Any]] = []
    if METRICS_FILE.exists():
        try:
            with open(METRICS_FILE, "r") as f:
                existing = json.load(f)
                existing_history = existing.get("round_history", [])
        except (json.JSONDecodeError, KeyError):
            existing_history = []

    round_history = _merge_round_history(existing_history, round_summary)

    return {
        "current_round": current_round,
        "snapshot": {
            "total_issues": metrics_data.get("total_issues", 0),
            "open_issues": metrics_data.get("open_issues", 0),
            "resolved_issues": metrics_data.get("resolved_issues", 0),
            "mean_ttr_seconds": metrics_data.get("mean_time_to_resolve_seconds"),
            "recurrence_count": metrics_data.get("recurrence_count", 0),
            "total_fix_attempts": metrics_data.get("total_fix_attempts", 0),
        },
        "escalation_summary": escalation_summary,
        "reviewer_stats": metrics_data.get("reviewer_stats", {}),
        "round_history": round_history,
        "timestamp": metrics_data.get("timestamp", ""),
    }


def write_metrics(metrics_summary: dict[str, Any]) -> None:
    """
    Write metrics summary to the metrics JSON file.

    Creates parent directories if needed.

    Args:
        metrics_summary: The complete metrics summary to write
    """
    METRICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(METRICS_FILE, "w") as f:
        json.dump(metrics_summary, f, indent=2)
    logger.info(f"Metrics written to {METRICS_FILE}")
