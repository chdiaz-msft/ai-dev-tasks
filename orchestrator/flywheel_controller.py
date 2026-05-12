"""
Main control loop for the PR Flywheel.

This module implements the orchestrator's decision engine:
- Loads and manages loop state across iterations
- Merges findings from reviewers and other signals
- Decides the next action (fixing, waiting, handoff, blocked, success)
- Writes decision outputs and GITHUB_OUTPUT for workflow consumption
- Enforces iteration caps and poll-interval constraints
"""

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.models import Issue, LoopState
from orchestrator.severity_classifier import filter_by_floor

# Constants
STATE_FILE = Path("state/loop-state.json")
MIN_POLL_INTERVAL_SECONDS = 300  # 5 minutes


def load_state(pr_number: int, max_rounds: int) -> dict[str, Any]:
    """
    Load or create loop state, increment current_round, set timestamp.

    Args:
        pr_number: Pull request number
        max_rounds: Maximum number of rounds allowed

    Returns:
        Dictionary representation of the loop state with incremented round
    """
    current_time = datetime.now(timezone.utc).isoformat()

    if STATE_FILE.exists():
        # Load existing state
        with open(STATE_FILE, "r") as f:
            state_data = json.load(f)
        loop_state = LoopState.from_dict(state_data)
        # Increment round
        loop_state.current_round += 1
        loop_state.last_round_timestamp = current_time
    else:
        # Create fresh state with current_round=1 (incremented from 0)
        loop_state = LoopState(
            pr_number=pr_number,
            current_round=1,
            max_rounds=max_rounds,
            termination=None,
            last_round_timestamp=current_time,
        )

    return loop_state.to_dict()


def resolve_issues_from_commit_messages(
    state: dict[str, Any], commit_messages: list[str]
) -> None:
    """
    Parse commit messages for [resolves: issue_id] patterns and mark issues resolved.

    Args:
        state: Current loop state dictionary
        commit_messages: List of commit message strings to parse
    """
    current_round = state.get("current_round", 0)
    issues = state.get("issues", {})

    # Pattern to match [resolves: issue_id] in commit messages
    pattern = re.compile(r"\[resolves:\s*([^\]]+)\]", re.IGNORECASE)

    for message in commit_messages:
        for match in pattern.finditer(message):
            issue_id = match.group(1).strip()
            if issue_id in issues and issues[issue_id].get("status") == "open":
                issues[issue_id]["status"] = "resolved"
                issues[issue_id]["resolved_in_round"] = current_round
                issues[issue_id]["resolution"] = "Marked resolved via commit message"


def merge_findings(
    state: dict[str, Any],
    findings: list[dict[str, Any]],
    commit_messages: list[str] | None = None,
) -> None:
    """
    Merge new findings into state, mark resolved issues.

    This function:
    - Adds new findings to state["issues"] with status="open"
    - Does NOT overwrite existing issues (idempotent)
    - Marks issues as resolved when their reviewer ran but the issue_id is absent
    - Does NOT mark issues as resolved if their reviewer did not run
    - Optionally resolves issues via [resolves: issue_id] patterns in commit messages

    Args:
        state: Current loop state dictionary
        findings: List of finding dictionaries from reviewers
        commit_messages: Optional list of commit message strings to parse for resolutions
    """
    current_round = state.get("current_round", 0)
    issues = state.setdefault("issues", {})

    # Track which reviewers ran in this round
    reviewers_that_ran = set(
        finding.get("reviewer", "") for finding in findings if finding.get("reviewer")
    )
    finding_ids = set(
        finding.get("issue_id", "") for finding in findings if finding.get("issue_id")
    )

    # Add new findings (do not overwrite existing)
    for finding in findings:
        issue_id = finding.get("issue_id")
        if not issue_id:
            continue  # Skip findings without issue_id

        if issue_id not in issues:
            # New issue - add it
            issues[issue_id] = {
                "issue_id": issue_id,
                "reviewer": finding.get("reviewer", "unknown"),
                "severity": finding.get("severity", "info"),
                "issue": finding.get("issue", ""),
                "suggested_fix": finding.get("suggested_fix", ""),
                "file": finding.get("file", ""),
                "line": finding.get("line"),
                "found_in_round": current_round,
                "status": "open",
                "resolved_in_round": None,
                "resolution": None,
            }

    # Mark issues as resolved if their reviewer ran but the issue_id is absent
    for issue_id, issue in issues.items():
        # Only process open issues
        if issue.get("status") == "open":
            reviewer = issue.get("reviewer", "")
            # If the reviewer ran this round but didn't flag this issue
            if reviewer in reviewers_that_ran and issue_id not in finding_ids:
                issue["status"] = "resolved"
                issue["resolved_in_round"] = current_round
                issue["resolution"] = (
                    f"No longer flagged by {reviewer} in round {current_round}"
                )

    # Process commit message resolutions if provided
    if commit_messages:
        resolve_issues_from_commit_messages(state, commit_messages)


def decide(
    state: dict[str, Any], severity_floor: str, signals: dict[str, Any]
) -> dict[str, Any]:
    """
    Decide the next action based on state and signals.

    Decision priority:
    1. Poll interval not elapsed → waiting
    2. Max rounds reached → handoff
    3. Human rejection → blocked
    4. Actionable issues exist → fixing
    5. No actionable issues but checks failing → waiting
    6. No actionable issues but incomplete reviewer coverage → waiting
    7. No actionable issues and checks green → ready

    Args:
        state: Current loop state dictionary
        severity_floor: Minimum severity level to consider actionable
        signals: Aggregated signals dictionary (checks, reviews, etc.)

    Returns:
        Decision dictionary with state, reason, and optionally dispatch
    """
    current_round = state["current_round"]
    max_rounds = state["max_rounds"]
    last_round_timestamp = state.get("last_round_timestamp")

    # 1. Check poll interval enforcement (skip for round 1)
    if current_round > 1 and last_round_timestamp:
        try:
            last_time = datetime.fromisoformat(
                last_round_timestamp.replace("Z", "+00:00")
            )
            now = datetime.now(timezone.utc)
            elapsed = (now - last_time).total_seconds()

            if elapsed < MIN_POLL_INTERVAL_SECONDS:
                retry_after = int(MIN_POLL_INTERVAL_SECONDS - elapsed)
                return {
                    "state": "waiting",
                    "reason": "poll_interval_not_elapsed",
                    "retry_after_seconds": retry_after,
                }
        except (ValueError, AttributeError):
            # If timestamp parsing fails, continue without enforcement
            pass

    # 2. Check max rounds
    if current_round >= max_rounds:
        return {"state": "handoff", "reason": "max_iterations_reached"}

    # 3. Check human rejection
    human_reviews = signals.get("human_reviews", [])
    for review in human_reviews:
        if review.get("state") == "CHANGES_REQUESTED":
            return {"state": "blocked", "reason": "human_rejection"}

    # 4. Compute actionable issues (open issues meeting severity floor)
    issues_dict = state.get("issues", {})
    # Convert to Issue objects for filtering
    issue_objects = {}
    for issue_id, issue_data in issues_dict.items():
        if issue_data.get("status") == "open":
            issue_objects[issue_id] = Issue.from_dict(issue_data)

    actionable = filter_by_floor(issue_objects, severity_floor)

    if actionable:
        # Have actionable issues → fixing
        # Cap at 10 items
        items = []
        for issue_id, issue in list(actionable.items())[:10]:
            items.append(issue.to_dict())

        return {
            "state": "fixing",
            "dispatch": {
                "agent": "claude",  # Default agent
                "items": items,
            },
        }

    # No actionable issues - check signals
    checks = signals.get("checks", [])
    reviewer_coverage = signals.get("reviewer_coverage", {})
    failed_reviewers = reviewer_coverage.get("failed", [])

    # 5. Check if reviewer coverage is incomplete
    if failed_reviewers:
        return {"state": "waiting", "reason": "incomplete_review_coverage"}

    # 6. Check if checks are green
    checks_green = all(
        check.get("conclusion") in ["success", "neutral", "skipped"] for check in checks
    )

    if not checks_green:
        return {"state": "waiting", "reason": "checks_not_green"}

    # 7. All clear → ready
    return {"state": "ready"}


def _write_github_output(decision: dict[str, Any], state: dict[str, Any]) -> None:
    """
    Write decision outputs to GITHUB_OUTPUT environment file.

    Args:
        decision: Decision dictionary from decide()
        state: Current state dictionary
    """
    github_output = os.environ.get("GITHUB_OUTPUT")
    if not github_output:
        return

    decision_state = decision.get("state", "unknown")
    current_round = state.get("current_round", 0)

    with open(github_output, "a") as f:
        f.write(f"state={decision_state}\n")
        f.write(f"iteration={current_round}\n")

        # Write dispatch as JSON if present
        dispatch = decision.get("dispatch")
        if dispatch:
            dispatch_json = json.dumps(dispatch)
            f.write(f"dispatch={dispatch_json}\n")
        else:
            f.write("dispatch=\n")

        # Determine if there are changes (fixing state indicates changes will be made)
        has_changes = "true" if decision_state == "fixing" else "false"
        f.write(f"has_changes={has_changes}\n")


def main() -> None:
    """
    Main entry point for the flywheel controller.

    Reads signals, loads state, merges findings, decides next action,
    writes decision.json and GITHUB_OUTPUT.
    """
    parser = argparse.ArgumentParser(
        description="PR Flywheel controller - decide next action"
    )
    parser.add_argument("--signals", required=True, help="Path to signals.json file")
    parser.add_argument("--max-iter", type=int, default=5, help="Maximum iterations")
    parser.add_argument(
        "--severity-floor",
        default="high",
        help="Minimum severity level for actionable issues",
    )
    parser.add_argument("--out", required=True, help="Output path for decision.json")

    args = parser.parse_args()

    # Load signals
    with open(args.signals, "r") as f:
        signals = json.load(f)

    pr_number = signals.get("pr_number", 0)
    swarm_findings = signals.get("swarm_findings", [])

    # Load or create state
    state = load_state(pr_number=pr_number, max_rounds=args.max_iter)

    # Merge findings
    merge_findings(state, swarm_findings)

    # Make decision
    decision = decide(state, args.severity_floor, signals)

    # Update termination status if terminal state reached
    decision_state = decision.get("state", "unknown")
    if decision_state in ["handoff", "blocked", "waiting", "ready"]:
        if decision_state == "ready":
            state["termination"] = "success"
        elif decision_state == "handoff":
            state["termination"] = "handoff"
        elif decision_state == "blocked":
            state["termination"] = "blocked"
        elif decision_state == "waiting":
            state["termination"] = "waiting"

    # Persist state
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

    # Write decision output
    with open(args.out, "w") as f:
        json.dump(decision, f, indent=2)

    # Write GITHUB_OUTPUT
    _write_github_output(decision, state)


if __name__ == "__main__":
    main()
