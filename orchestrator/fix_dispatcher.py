"""
Fix agent selection and dispatch payload building for the PR Flywheel.

This module handles routing fixes to the appropriate agent:
- select_agent: chooses between Claude Code and GitHub Copilot Agent
- build_dispatch_payload: constructs the dispatch payload with items capped at 10
- validate_forbidden_paths: enforces path allowlists (blocks .github/workflows/**, **/secrets/**)
- get_forbidden_files: returns the subset of files matching forbidden patterns
- Ensures safe, bounded fix operations
"""

import re

# Forbidden path patterns
FORBIDDEN_PATTERNS = [
    r"^\.github/workflows/",  # Block workflow files
    r"secrets",  # Block any path containing "secrets"
]

MAX_DISPATCH_ITEMS = 10


def select_agent(enable_claude: bool, enable_copilot_agent: bool) -> str:
    """
    Select the fix agent based on enabled flags.

    Prefers Claude when both are enabled. Raises ValueError if neither is enabled.

    Args:
        enable_claude: Whether Claude Code fix agent is enabled
        enable_copilot_agent: Whether GitHub Copilot Agent is enabled

    Returns:
        The selected agent name: "claude" or "copilot"

    Raises:
        ValueError: If both flags are False
    """
    if not enable_claude and not enable_copilot_agent:
        raise ValueError("At least one fix agent must be enabled")

    # Prefer Claude when both are enabled
    if enable_claude:
        return "claude"

    return "copilot"


def build_dispatch_payload(
    agent: str, actionable_items: list[dict[str, object]]
) -> dict[str, object]:
    """
    Build a dispatch payload for the fix agent.

    Caps items at 10 maximum, preserving insertion order.
    Sets prompt_path to "prompts/claude-fix-prompt.md" for Claude, None for Copilot.

    Args:
        agent: The selected agent name ("claude" or "copilot")
        actionable_items: List of actionable issue dicts

    Returns:
        A dict with keys: agent, items, prompt_path
    """
    # Cap items at MAX_DISPATCH_ITEMS, preserving order
    capped_items = actionable_items[:MAX_DISPATCH_ITEMS]

    # Set prompt_path based on agent
    prompt_path = "prompts/claude-fix-prompt.md" if agent == "claude" else None

    return {
        "agent": agent,
        "items": capped_items,
        "prompt_path": prompt_path,
    }


def validate_forbidden_paths(changed_files: list[str]) -> bool:
    """
    Validate that no changed files match forbidden path patterns.

    Forbidden patterns:
    - .github/workflows/** (workflow files)
    - **/secrets/** (any path containing "secrets")

    Args:
        changed_files: List of file paths to validate

    Returns:
        True if all paths are allowed, False if any path is forbidden
    """
    for file in changed_files:
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, file):
                return False
    return True


def get_forbidden_files(changed_files: list[str]) -> list[str]:
    """
    Get the subset of files that match forbidden path patterns.

    Preserves the original order of files.

    Args:
        changed_files: List of file paths to check

    Returns:
        List of files that match forbidden patterns
    """
    forbidden = []
    for file in changed_files:
        for pattern in FORBIDDEN_PATTERNS:
            if re.search(pattern, file):
                forbidden.append(file)
                break  # Don't add the same file multiple times
    return forbidden
