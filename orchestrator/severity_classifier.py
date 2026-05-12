"""
Severity classification and filtering for the PR Flywheel.

This module defines the severity scale and filtering logic:
- SEV_ORDER mapping: critical=4, high=3, medium=2, low=1, info=0
- Comparison helpers: severity_gte, severity_lt
- filter_by_floor: filters issues to only those meeting the severity threshold
- classify_severity: normalizes raw severity strings to the canonical scale
- Supports severity-driven actionability decisions
"""

from orchestrator.models import Issue

# Severity order mapping: higher number = more severe
SEV_ORDER: dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "info": 0,
}


def severity_gte(a: str, b: str) -> bool:
    """
    Check if severity 'a' is greater than or equal to severity 'b'.

    Args:
        a: First severity level
        b: Second severity level

    Returns:
        True if a >= b in the severity scale, False otherwise
    """
    return SEV_ORDER[a] >= SEV_ORDER[b]


def severity_lt(a: str, b: str) -> bool:
    """
    Check if severity 'a' is strictly less than severity 'b'.

    Args:
        a: First severity level
        b: Second severity level

    Returns:
        True if a < b in the severity scale, False otherwise
    """
    return SEV_ORDER[a] < SEV_ORDER[b]


def filter_by_floor(issues: dict[str, Issue], floor: str) -> dict[str, Issue]:
    """
    Filter issues to only those meeting the severity threshold.

    Returns issues where severity >= floor. Preserves insertion order.

    Args:
        issues: Dictionary mapping issue_id to Issue objects
        floor: Minimum severity level to keep

    Returns:
        Dictionary of issues meeting or exceeding the severity floor,
        preserving insertion order from the input dict
    """
    return {
        issue_id: issue
        for issue_id, issue in issues.items()
        if severity_gte(issue.severity, floor)
    }


def classify_severity(raw: str) -> str:
    """
    Normalize a raw severity string to the canonical scale.

    Converts to lowercase and returns 'info' for unknown values.

    Args:
        raw: Raw severity string (may be uppercase, mixed case, or unknown)

    Returns:
        Normalized severity level: one of critical, high, medium, low, info
    """
    normalized = raw.lower()
    if normalized in SEV_ORDER:
        return normalized
    return "info"
