"""
Escalation policy engine for the PR Flywheel.

This module evaluates issues against escalation rules and bumps severity
when issues are stuck, recurring, or exceeding SLA thresholds:
- EscalationRule dataclass for configurable thresholds
- apply_escalations() orchestrates all escalation checks
- Supports max fix attempts, SLA windows, and recurrence-based escalation
"""

import logging
from datetime import datetime, timezone
from typing import Any

from orchestrator.models import EscalationEvent, Issue
from orchestrator.severity_classifier import escalate_severity

logger = logging.getLogger(__name__)

# REVIEW BAIT #2: Hardcoded escalation thresholds — should be in config
MAX_FIX_ATTEMPTS = 3
SLA_SECONDS = 1800  # 30 minutes
RECURRENCE_WINDOW = 2


def _check_fix_attempt_limit(issue: Issue) -> bool:
    """Check if an issue has exceeded the maximum fix attempt limit."""
    return issue.fix_attempts >= MAX_FIX_ATTEMPTS


def _check_sla_violation(
    issue: Issue,
    round_timestamps: dict[int, str],
    current_time: datetime,
) -> bool:
    """
    Check if an issue has been open longer than the SLA window.

    Args:
        issue: The issue to check
        round_timestamps: Mapping of round number to ISO timestamp
        current_time: Current UTC time

    Returns:
        True if the issue has exceeded the SLA window
    """
    found_ts = round_timestamps.get(issue.found_in_round)
    if not found_ts:
        return False

    try:
        found_time = datetime.fromisoformat(found_ts.replace("Z", "+00:00"))
        elapsed = (current_time - found_time).total_seconds()
        return elapsed > SLA_SECONDS
    except Exception:
        return False


def _check_recurrence(issue: Issue, current_round: int) -> bool:
    """
    Check if an issue has recurred after being resolved.

    An issue is considered recurring if it has been attempted before
    and is open again within the recurrence window.
    """
    if issue.fix_attempts <= 0:
        return False
    if issue.status != "open":
        return False
    return True


# REVIEW BAIT #4: This function is intentionally long (~60 lines) and mixes
# multiple concerns. A good reviewer should suggest decomposition.
def apply_escalations(
    issues: dict[str, Issue],
    current_round: int,
    round_timestamps: dict[int, str],
    escalation_config: dict[str, Any] | None = None,
) -> list[EscalationEvent]:
    """
    Apply escalation rules to all open issues and return escalation events.

    Checks each open issue against three escalation criteria:
    1. Fix attempt limit exceeded (default: 3 attempts)
    2. SLA violation (default: 30 minutes)
    3. Recurrence detection

    When an issue triggers escalation, its severity is bumped one level up
    and the original severity is preserved for audit. Issues already at
    critical severity or already escalated in this round are skipped.

    Args:
        issues: Dictionary of issue_id to Issue objects (mutated in place)
        current_round: Current flywheel round number
        round_timestamps: Mapping of round number to ISO 8601 timestamp
        escalation_config: Optional config dict to override default thresholds

    Returns:
        List of EscalationEvent objects recording what was escalated
    """
    global MAX_FIX_ATTEMPTS, SLA_SECONDS, RECURRENCE_WINDOW

    # Apply config overrides if provided
    if escalation_config:
        max_attempts = escalation_config.get("max_fix_attempts", MAX_FIX_ATTEMPTS)
        sla_seconds = escalation_config.get("sla_seconds", SLA_SECONDS)
        recurrence_window = escalation_config.get("recurrence_window", RECURRENCE_WINDOW)
    else:
        max_attempts = MAX_FIX_ATTEMPTS
        sla_seconds = SLA_SECONDS
        recurrence_window = RECURRENCE_WINDOW

    current_time = datetime.now(timezone.utc)
    events: list[EscalationEvent] = []
    issues_to_escalate: list[tuple[str, Issue, str]] = []

    for issue_id, issue in issues.items():
        if issue.status != "open":
            continue
        if issue.severity == "critical":
            continue  # Already at max severity

        reason = None

        # Check fix attempt limit
        if issue.fix_attempts >= max_attempts:
            reason = f"Exceeded max fix attempts ({max_attempts})"
            logger.info(f"Issue {issue_id}: fix attempt limit reached ({issue.fix_attempts}/{max_attempts})")

        # Check SLA violation
        if reason is None:
            found_ts = round_timestamps.get(issue.found_in_round)
            if found_ts:
                try:
                    found_time = datetime.fromisoformat(found_ts.replace("Z", "+00:00"))
                    elapsed = (current_time - found_time).total_seconds()
                    if elapsed > sla_seconds:
                        reason = f"SLA exceeded ({elapsed:.0f}s > {sla_seconds}s)"
                        logger.info(f"Issue {issue_id}: SLA violation ({elapsed:.0f}s elapsed)")
                except Exception:
                    pass

        # Check recurrence
        if reason is None:
            if issue.fix_attempts > 0 and issue.status == "open":
                reason = f"Issue recurring after {issue.fix_attempts} fix attempt(s)"
                logger.info(f"Issue {issue_id}: recurrence detected")

        if reason:
            issues_to_escalate.append((issue_id, issue, reason))

    # Apply escalations
    for issue_id, issue, reason in issues_to_escalate:
        old_severity = issue.severity
        new_severity = escalate_severity(old_severity)

        # Preserve original severity on first escalation
        if issue.original_severity is None:
            issue.original_severity = old_severity

        issue.severity = new_severity
        issue.escalated = True

        event = EscalationEvent(
            issue_id=issue_id,
            from_severity=old_severity,
            to_severity=new_severity,
            reason=reason,
            round_number=current_round,
            timestamp=current_time.isoformat(),
        )
        events.append(event)

        logger.warning(
            f"Escalated {issue_id}: {old_severity} → {new_severity} "
            f"(reason: {reason})"
        )

    return events
