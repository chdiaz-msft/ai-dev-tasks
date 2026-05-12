"""
Parallel reviewer dispatch for the PR Flywheel.

This module coordinates multiple specialized reviewers (correctness, security, etc.):
- Dispatches reviewers in parallel using ThreadPoolExecutor
- Enforces timeout constraints (default 120 seconds)
- Generates deterministic issue IDs for stable tracking across rounds
- Returns structured findings and reviewer coverage metadata
- Handles graceful degradation when individual reviewers fail
"""

import hashlib
import json
from concurrent.futures import Future, ThreadPoolExecutor, as_completed, TimeoutError
from pathlib import Path
from typing import Any

# List of all reviewers to dispatch
REVIEWERS = ["correctness", "security"]

# Timeout in seconds for each reviewer
TIMEOUT_SECONDS = 120


def generate_issue_id(
    reviewer: str, file: str, line: int | None, issue_text: str
) -> str:
    """
    Generate a deterministic issue ID for stable tracking across rounds.

    Format: {reviewer}-{sha256(file+line+issue_text)[:8]}

    Args:
        reviewer: Name of the reviewer (e.g., "correctness", "security")
        file: File path where the issue was found
        line: Line number (or None for file-level issues)
        issue_text: Description of the issue

    Returns:
        Deterministic issue ID string
    """
    # Use empty string for None line to differentiate from line=0
    line_str = "" if line is None else str(line)

    # Create hash input from file, line, and issue text
    hash_input = f"{file}{line_str}{issue_text}"

    # Generate SHA256 hash and take first 8 characters
    digest = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()[:8]

    return f"{reviewer}-{digest}"


def _call_reviewer_api(prompt: str, reviewer_input: str) -> list[dict[str, Any]]:
    """
    Pluggable stub for calling the reviewer API.

    This function is designed to be replaced with actual API call logic.
    For now, it returns an empty list to support testing.

    Args:
        prompt: System prompt for the reviewer
        reviewer_input: JSON string with diff, doctrine, and prior issues

    Returns:
        List of finding dicts matching the structured schema
    """
    # Stub implementation - to be replaced with actual API call
    return []


def _load_prompt(reviewer: str) -> str:
    """
    Load the prompt template for a given reviewer.

    Args:
        reviewer: Name of the reviewer (e.g., "correctness", "security")

    Returns:
        Prompt template content as a string
    """
    prompt_path = Path(f"prompts/reviewers/{reviewer}.md")

    if prompt_path.exists():
        return prompt_path.read_text(encoding="utf-8")
    else:
        # Fallback prompt if template doesn't exist yet
        return f"You are a {reviewer} reviewer. Analyze the code and return findings."


def run_reviewer(
    reviewer: str, diff: str, doctrine: str, loop_state: dict[str, Any]
) -> list[dict[str, Any]]:
    """
    Execute a single reviewer and return structured findings.

    Args:
        reviewer: Name of the reviewer (e.g., "correctness", "security")
        diff: Git diff string for the PR
        doctrine: Review doctrine/guidelines
        loop_state: Current loop state dict with pr_number, current_round, issues

    Returns:
        List of finding dicts, each with keys:
        - issue_id: Unique identifier
        - reviewer: Reviewer name
        - severity: critical/high/medium/low/info
        - issue: Description of the issue
        - suggested_fix: Suggested fix
        - file: File path
        - line: Line number (or None)
    """
    # Load reviewer prompt template
    prompt = _load_prompt(reviewer)

    # Build input JSON
    reviewer_input_data: dict[str, Any] = {
        "reviewer": reviewer,
        "diff": diff,
        "doctrine": doctrine,
        "prior_issues": loop_state.get("issues", {}),
    }
    reviewer_input: str = json.dumps(reviewer_input_data)

    # Call the reviewer API (stub for now)
    findings: list[dict[str, Any]] = _call_reviewer_api(prompt, reviewer_input)

    return findings


def dispatch_swarm(
    diff: str, doctrine: str, loop_state: dict[str, Any]
) -> dict[str, Any]:
    """
    Dispatch all reviewers in parallel with timeout and error handling.

    Uses ThreadPoolExecutor to run reviewers concurrently with a 2-minute
    timeout per reviewer. Handles exceptions gracefully and tracks coverage.

    Args:
        diff: Git diff string for the PR
        doctrine: Review doctrine/guidelines
        loop_state: Current loop state dict

    Returns:
        Dict with keys:
        - findings: List of all findings from successful reviewers
        - reviewer_coverage: Dict with expected, completed, failed lists
    """
    all_findings: list[dict[str, Any]] = []
    completed_reviewers: list[str] = []
    failed_reviewers: list[dict[str, str]] = []

    # Create a thread pool with one worker per reviewer
    with ThreadPoolExecutor(max_workers=len(REVIEWERS)) as executor:
        # Submit all reviewers
        future_to_reviewer: dict[Future[list[dict[str, Any]]], str] = {
            executor.submit(
                run_reviewer, reviewer, diff, doctrine, loop_state
            ): reviewer
            for reviewer in REVIEWERS
        }

        # Collect results with timeout
        try:
            for future in as_completed(future_to_reviewer, timeout=TIMEOUT_SECONDS):
                reviewer: str = future_to_reviewer[future]

                try:
                    # Get the findings from this reviewer
                    findings: list[dict[str, Any]] = future.result()
                    all_findings.extend(findings)
                    completed_reviewers.append(reviewer)

                except Exception as exc:
                    # Reviewer failed - record the error
                    error_msg: str = str(exc)
                    failed_reviewers.append({"reviewer": reviewer, "error": error_msg})

        except TimeoutError:
            # Global timeout - mark any unfinished reviewers as failed
            for future, reviewer in future_to_reviewer.items():
                if not future.done():
                    failed_reviewers.append(
                        {
                            "reviewer": reviewer,
                            "error": "Reviewer exceeded 120-second timeout",
                        }
                    )

    # Build reviewer coverage report
    reviewer_coverage: dict[str, Any] = {
        "expected": REVIEWERS.copy(),
        "completed": completed_reviewers,
        "failed": failed_reviewers,
    }

    return {"findings": all_findings, "reviewer_coverage": reviewer_coverage}
