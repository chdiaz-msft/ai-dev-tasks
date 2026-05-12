"""
Data models for the PR Flywheel.

This module defines the core data structures:
- TerminationStatus: SUCCESS, HANDOFF, BLOCKED, WAITING
- IssueStatus: OPEN, RESOLVED, WONT_FIX, ESCALATED
- Issue: represents a single review finding with metadata (severity, file, line, status, resolution)
- LoopState: represents the persistent state across flywheel iterations (round, issues, termination)
- Provides serialization/deserialization (to_dict/from_dict) for state persistence
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, cast


class TerminationStatus(StrEnum):
    """Enumeration of possible termination states for the flywheel loop."""

    SUCCESS = "success"
    HANDOFF = "handoff"
    BLOCKED = "blocked"
    WAITING = "waiting"


class IssueStatus(StrEnum):
    """Enumeration of possible issue statuses."""

    OPEN = "open"
    RESOLVED = "resolved"
    WONT_FIX = "wont_fix"
    ESCALATED = "escalated"
    ABANDONED = "abandoned"


@dataclass
class EscalationEvent:
    """
    Records a severity escalation event for audit purposes.

    Attributes:
        issue_id: ID of the escalated issue
        from_severity: Original severity level before escalation
        to_severity: New severity level after escalation
        reason: Why the escalation occurred
        round_number: Round in which the escalation happened
        timestamp: ISO 8601 timestamp of the escalation
    """

    issue_id: str
    from_severity: str
    to_severity: str
    reason: str
    round_number: int
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        """Convert EscalationEvent to a dictionary for serialization."""
        return {
            "issue_id": self.issue_id,
            "from_severity": self.from_severity,
            "to_severity": self.to_severity,
            "reason": self.reason,
            "round_number": self.round_number,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EscalationEvent":
        """Create an EscalationEvent from a dictionary."""
        return cls(
            issue_id=str(data["issue_id"]),
            from_severity=str(data["from_severity"]),
            to_severity=str(data["to_severity"]),
            reason=str(data["reason"]),
            round_number=int(data["round_number"]),
            timestamp=str(data["timestamp"]),
        )


@dataclass
class Issue:
    """
    Represents a single review finding.

    Attributes:
        issue_id: Unique identifier for the issue
        reviewer: Name of the reviewer that found this issue
        severity: Severity level (critical, high, medium, low, info)
        issue: Description of the issue
        suggested_fix: Suggested fix for the issue
        file: File path where the issue was found
        line: Line number where the issue was found (optional)
        found_in_round: Round number when the issue was first found
        status: Current status of the issue (default: "open")
        resolved_in_round: Round number when the issue was resolved (optional)
        resolution: Description of how the issue was resolved (optional)
        fix_attempts: Number of times the flywheel has attempted to fix this issue
        original_severity: The reviewer's original severity before any escalation
        escalated: Whether this issue has been escalated from its original severity
    """

    issue_id: str
    reviewer: str
    severity: str
    issue: str
    suggested_fix: str
    file: str
    line: int | None
    found_in_round: int
    status: str = "open"
    resolved_in_round: int | None = None
    resolution: str | None = None
    fix_attempts: int = 0
    original_severity: str | None = None
    escalated: bool = False

    def to_dict(self) -> dict[str, Any]:
        """
        Convert Issue to a dictionary for serialization.

        Returns:
            Dictionary representation of the issue
        """
        return {
            "issue_id": self.issue_id,
            "reviewer": self.reviewer,
            "severity": self.severity,
            "issue": self.issue,
            "suggested_fix": self.suggested_fix,
            "file": self.file,
            "line": self.line,
            "found_in_round": self.found_in_round,
            "status": self.status,
            "resolved_in_round": self.resolved_in_round,
            "resolution": self.resolution,
            "fix_attempts": self.fix_attempts,
            "original_severity": self.original_severity,
            "escalated": self.escalated,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Issue":
        """
        Create an Issue from a dictionary.

        Args:
            data: Dictionary containing issue data

        Returns:
            Issue instance constructed from the dictionary data
        """
        line_val = data["line"]
        resolved_round_val = data.get("resolved_in_round")
        resolution_val = data.get("resolution")
        original_sev_val = data.get("original_severity")

        return cls(
            issue_id=str(data["issue_id"]),
            reviewer=str(data["reviewer"]),
            severity=str(data["severity"]),
            issue=str(data["issue"]),
            suggested_fix=str(data["suggested_fix"]),
            file=str(data["file"]),
            line=int(line_val) if line_val is not None else None,
            found_in_round=int(data["found_in_round"]),
            status=str(data.get("status", "open")),
            resolved_in_round=int(resolved_round_val)
            if resolved_round_val is not None
            else None,
            resolution=str(resolution_val) if resolution_val is not None else None,
            fix_attempts=int(data.get("fix_attempts", 0)),
            original_severity=str(original_sev_val) if original_sev_val is not None else None,
            escalated=bool(data.get("escalated", False)),
        )


@dataclass
class LoopState:
    """
    Represents the persistent state of the flywheel loop across iterations.

    Attributes:
        pr_number: Pull request number
        current_round: Current iteration round (default: 0)
        max_rounds: Maximum number of rounds allowed (default: 5)
        issues: Dictionary of issues by issue_id
        termination: Termination status (optional)
        last_round_timestamp: ISO 8601 timestamp of the last round (optional)
        escalation_events: List of escalation events that have occurred
    """

    pr_number: int
    current_round: int = 0
    max_rounds: int = 5
    issues: dict[str, Issue] = field(default_factory=dict)
    termination: str | None = None
    last_round_timestamp: str | None = None
    escalation_events: list[EscalationEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        Convert LoopState to a dictionary for serialization.

        Returns:
            Dictionary representation of the loop state
        """
        return {
            "pr_number": self.pr_number,
            "current_round": self.current_round,
            "max_rounds": self.max_rounds,
            "issues": {
                issue_id: issue.to_dict() for issue_id, issue in self.issues.items()
            },
            "termination": self.termination,
            "last_round_timestamp": self.last_round_timestamp,
            "escalation_events": [event.to_dict() for event in self.escalation_events],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LoopState":
        """
        Create a LoopState from a dictionary.

        Args:
            data: Dictionary containing loop state data

        Returns:
            LoopState instance constructed from the dictionary data
        """
        issues_data = data.get("issues", {})
        if isinstance(issues_data, dict):
            issues = {
                issue_id: Issue.from_dict(cast(dict[str, Any], issue_dict))
                for issue_id, issue_dict in issues_data.items()
                if isinstance(issue_dict, dict)
            }
        else:
            issues = {}

        pr_number_val = data["pr_number"]
        current_round_val = data.get("current_round", 0)
        max_rounds_val = data.get("max_rounds", 5)
        termination_val = data.get("termination")
        timestamp_val = data.get("last_round_timestamp")

        escalation_events_data = data.get("escalation_events", [])
        escalation_events = [
            EscalationEvent.from_dict(cast(dict[str, Any], event_dict))
            for event_dict in escalation_events_data
            if isinstance(event_dict, dict)
        ]

        return cls(
            pr_number=int(pr_number_val),
            current_round=int(current_round_val),
            max_rounds=int(max_rounds_val),
            issues=issues,
            termination=str(termination_val) if termination_val is not None else None,
            last_round_timestamp=str(timestamp_val)
            if timestamp_val is not None
            else None,
            escalation_events=escalation_events,
        )
