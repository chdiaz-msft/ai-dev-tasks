"""
Tests for orchestrator.fix_dispatcher module.

Tests cover:
- select_agent: agent selection based on enable flags
- select_agent: prefers Claude when both flags are True
- select_agent: raises ValueError when both flags are False
- build_dispatch_payload: constructs payload with correct structure
- build_dispatch_payload: caps items at 10 when more are provided
- build_dispatch_payload: preserves item order (first 10 by insertion order)
- validate_forbidden_paths: returns True for allowed paths
- validate_forbidden_paths: returns False for .github/workflows/** paths
- validate_forbidden_paths: returns False for **/secrets/** paths
- validate_forbidden_paths: edge cases (.github/copilot-instructions.md allowed, .github/workflows/ci.yml forbidden)
- get_forbidden_files: returns only the forbidden files from the list
"""

import pytest
from orchestrator.fix_dispatcher import (
    select_agent,
    build_dispatch_payload,
    validate_forbidden_paths,
    get_forbidden_files,
)


class TestSelectAgent:
    """Tests for the select_agent function."""

    def test_select_agent_returns_claude_when_enabled(self):
        """Test that select_agent returns 'claude' when enable_claude=True."""
        result = select_agent(enable_claude=True, enable_copilot_agent=False)
        assert result == "claude"

    def test_select_agent_returns_copilot_when_enabled_and_claude_disabled(self):
        """Test that select_agent returns 'copilot' when enable_copilot_agent=True and enable_claude=False."""
        result = select_agent(enable_claude=False, enable_copilot_agent=True)
        assert result == "copilot"

    def test_select_agent_raises_error_when_both_false(self):
        """Test that select_agent raises ValueError when both flags are False."""
        with pytest.raises(ValueError, match="At least one fix agent must be enabled"):
            select_agent(enable_claude=False, enable_copilot_agent=False)

    def test_select_agent_prefers_claude_when_both_true(self):
        """Test that select_agent prefers Claude when both flags are True."""
        result = select_agent(enable_claude=True, enable_copilot_agent=True)
        assert result == "claude"


class TestBuildDispatchPayload:
    """Tests for the build_dispatch_payload function."""

    def test_build_dispatch_payload_structure(self):
        """Test that build_dispatch_payload returns a dict with keys agent, items, and prompt_path."""
        actionable_items = [
            {
                "issue_id": "correctness-abc12345",
                "reviewer": "correctness",
                "severity": "high",
                "issue": "Null pointer dereference",
                "suggested_fix": "Add null check",
                "file": "src/main.py",
                "line": 42,
            }
        ]

        result = build_dispatch_payload(
            agent="claude", actionable_items=actionable_items
        )

        assert isinstance(result, dict)
        assert "agent" in result
        assert "items" in result
        assert "prompt_path" in result
        assert result["agent"] == "claude"
        assert result["items"] == actionable_items
        assert result["prompt_path"] == "prompts/claude-fix-prompt.md"

    def test_build_dispatch_payload_copilot_no_prompt_path(self):
        """Test that build_dispatch_payload sets prompt_path to None for Copilot agent."""
        actionable_items = [
            {
                "issue_id": "security-def67890",
                "reviewer": "security",
                "severity": "critical",
                "issue": "SQL injection",
                "suggested_fix": "Use parameterized queries",
                "file": "src/db.py",
                "line": 100,
            }
        ]

        result = build_dispatch_payload(
            agent="copilot", actionable_items=actionable_items
        )

        assert result["agent"] == "copilot"
        assert result["prompt_path"] is None

    def test_build_dispatch_payload_caps_items_at_10(self):
        """Test that build_dispatch_payload caps items at 10 when more than 10 are provided."""
        # Create 15 actionable items
        actionable_items = [
            {
                "issue_id": f"correctness-{i:08x}",
                "reviewer": "correctness",
                "severity": "high",
                "issue": f"Issue {i}",
                "suggested_fix": f"Fix {i}",
                "file": f"src/file{i}.py",
                "line": i,
            }
            for i in range(15)
        ]

        result = build_dispatch_payload(
            agent="claude", actionable_items=actionable_items
        )

        assert len(result["items"]) == 10
        assert result["items"] == actionable_items[:10]

    def test_build_dispatch_payload_preserves_item_order(self):
        """Test that build_dispatch_payload preserves item order (first 10 by insertion order)."""
        # Create 12 actionable items with identifiable IDs
        actionable_items = [
            {
                "issue_id": f"item-{i}",
                "reviewer": "correctness",
                "severity": "high",
                "issue": f"Issue {i}",
                "suggested_fix": f"Fix {i}",
                "file": f"src/file{i}.py",
                "line": i,
            }
            for i in range(12)
        ]

        result = build_dispatch_payload(
            agent="claude", actionable_items=actionable_items
        )

        # Verify that the first 10 items are in the correct order
        assert len(result["items"]) == 10
        for i in range(10):
            assert result["items"][i]["issue_id"] == f"item-{i}"

    def test_build_dispatch_payload_with_empty_items(self):
        """Test that build_dispatch_payload handles empty items list."""
        result = build_dispatch_payload(agent="claude", actionable_items=[])

        assert result["agent"] == "claude"
        assert result["items"] == []
        assert result["prompt_path"] == "prompts/claude-fix-prompt.md"

    def test_build_dispatch_payload_with_exactly_10_items(self):
        """Test that build_dispatch_payload handles exactly 10 items correctly."""
        actionable_items = [
            {
                "issue_id": f"correctness-{i:08x}",
                "reviewer": "correctness",
                "severity": "high",
                "issue": f"Issue {i}",
                "suggested_fix": f"Fix {i}",
                "file": f"src/file{i}.py",
                "line": i,
            }
            for i in range(10)
        ]

        result = build_dispatch_payload(
            agent="claude", actionable_items=actionable_items
        )

        assert len(result["items"]) == 10
        assert result["items"] == actionable_items


class TestValidateForbiddenPaths:
    """Tests for the validate_forbidden_paths function."""

    def test_validate_forbidden_paths_returns_true_for_allowed_paths(self):
        """Test that validate_forbidden_paths returns True when no forbidden paths are present."""
        allowed_files = [
            "src/main.py",
            "src/db.py",
            "tests/test_main.py",
            "README.md",
            ".github/copilot-instructions.md",
        ]

        result = validate_forbidden_paths(allowed_files)
        assert result is True

    def test_validate_forbidden_paths_returns_false_for_workflows(self):
        """Test that validate_forbidden_paths returns False when any path matches .github/workflows/**."""
        files_with_workflow = [
            "src/main.py",
            ".github/workflows/ci.yml",
            "tests/test_main.py",
        ]

        result = validate_forbidden_paths(files_with_workflow)
        assert result is False

    def test_validate_forbidden_paths_returns_false_for_secrets(self):
        """Test that validate_forbidden_paths returns False when any path matches **/secrets/**."""
        files_with_secrets = [
            "src/main.py",
            "config/secrets/api_keys.json",
            "tests/test_main.py",
        ]

        result = validate_forbidden_paths(files_with_secrets)
        assert result is False

    def test_validate_forbidden_paths_edge_case_copilot_instructions_allowed(self):
        """Test that .github/copilot-instructions.md is allowed."""
        files = [
            "src/main.py",
            ".github/copilot-instructions.md",
            ".github/CODEOWNERS",
        ]

        result = validate_forbidden_paths(files)
        assert result is True

    def test_validate_forbidden_paths_edge_case_workflow_forbidden(self):
        """Test that .github/workflows/ci.yml is forbidden."""
        files = [
            "src/main.py",
            ".github/workflows/ci.yml",
        ]

        result = validate_forbidden_paths(files)
        assert result is False

    def test_validate_forbidden_paths_with_empty_list(self):
        """Test that validate_forbidden_paths returns True for an empty list."""
        result = validate_forbidden_paths([])
        assert result is True

    def test_validate_forbidden_paths_multiple_forbidden_patterns(self):
        """Test that validate_forbidden_paths returns False when multiple forbidden patterns are present."""
        files = [
            "src/main.py",
            ".github/workflows/deploy.yml",
            "backend/secrets/db_credentials.json",
        ]

        result = validate_forbidden_paths(files)
        assert result is False

    def test_validate_forbidden_paths_secrets_in_different_locations(self):
        """Test that secrets pattern matches in various directory structures."""
        test_cases = [
            (["src/secrets/keys.json"], False),
            (["secrets/api.json"], False),
            (["config/app/secrets/tokens.json"], False),
            (["src/secret_manager.py"], True),  # "secret" not "secrets"
        ]

        for files, expected in test_cases:
            result = validate_forbidden_paths(files)
            assert result == expected, f"Failed for {files}"


class TestGetForbiddenFiles:
    """Tests for the get_forbidden_files function."""

    def test_get_forbidden_files_returns_only_forbidden(self):
        """Test that get_forbidden_files returns only the forbidden files from the list."""
        files = [
            "src/main.py",
            ".github/workflows/ci.yml",
            "tests/test_main.py",
            "config/secrets/api_keys.json",
            "README.md",
        ]

        result = get_forbidden_files(files)

        assert len(result) == 2
        assert ".github/workflows/ci.yml" in result
        assert "config/secrets/api_keys.json" in result

    def test_get_forbidden_files_returns_empty_for_all_allowed(self):
        """Test that get_forbidden_files returns an empty list when all files are allowed."""
        files = [
            "src/main.py",
            "tests/test_main.py",
            "README.md",
            ".github/copilot-instructions.md",
        ]

        result = get_forbidden_files(files)
        assert result == []

    def test_get_forbidden_files_returns_all_if_all_forbidden(self):
        """Test that get_forbidden_files returns all files if all are forbidden."""
        files = [
            ".github/workflows/ci.yml",
            ".github/workflows/deploy.yml",
            "config/secrets/db.json",
        ]

        result = get_forbidden_files(files)

        assert len(result) == 3
        assert set(result) == set(files)

    def test_get_forbidden_files_with_empty_list(self):
        """Test that get_forbidden_files returns an empty list for an empty input."""
        result = get_forbidden_files([])
        assert result == []

    def test_get_forbidden_files_preserves_order(self):
        """Test that get_forbidden_files preserves the order of forbidden files."""
        files = [
            "src/main.py",
            ".github/workflows/ci.yml",
            "tests/test_main.py",
            ".github/workflows/deploy.yml",
            "config/secrets/api_keys.json",
        ]

        result = get_forbidden_files(files)

        # The order should be preserved (workflows first, then secrets)
        assert result == [
            ".github/workflows/ci.yml",
            ".github/workflows/deploy.yml",
            "config/secrets/api_keys.json",
        ]
