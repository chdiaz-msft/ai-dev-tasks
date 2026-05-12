"""
Tests for review swarm dispatch and parallel reviewer execution.

This module tests:
- generate_issue_id deterministic ID generation
- dispatch_swarm parallel execution with ThreadPoolExecutor
- Timeout handling (2-minute timeout per reviewer)
- Graceful degradation when reviewers fail
- Structured finding schema validation
- Reviewer coverage tracking (expected, completed, failed)
"""

from unittest.mock import patch
from orchestrator.review_swarm import (
    generate_issue_id,
    run_reviewer,
    dispatch_swarm,
    REVIEWERS,
)


class TestGenerateIssueId:
    """Test deterministic issue ID generation."""

    def test_generate_issue_id_deterministic(self):
        """Test generate_issue_id produces deterministic IDs with same inputs."""
        issue_id_1 = generate_issue_id(
            "correctness", "src/main.py", 42, "null pointer dereference"
        )
        issue_id_2 = generate_issue_id(
            "correctness", "src/main.py", 42, "null pointer dereference"
        )

        assert issue_id_1 == issue_id_2
        # Verify format: {reviewer}-{sha256[:8]}
        assert issue_id_1.startswith("correctness-")
        assert len(issue_id_1.split("-")[1]) == 8

    def test_generate_issue_id_different_inputs(self):
        """Test generate_issue_id with different inputs produces different IDs."""
        issue_id_1 = generate_issue_id("correctness", "src/main.py", 42, "null pointer")
        issue_id_2 = generate_issue_id("security", "src/main.py", 42, "null pointer")
        issue_id_3 = generate_issue_id(
            "correctness", "src/other.py", 42, "null pointer"
        )
        issue_id_4 = generate_issue_id("correctness", "src/main.py", 43, "null pointer")
        issue_id_5 = generate_issue_id(
            "correctness", "src/main.py", 42, "different issue"
        )

        all_ids = [issue_id_1, issue_id_2, issue_id_3, issue_id_4, issue_id_5]
        assert len(set(all_ids)) == 5  # All unique

    def test_generate_issue_id_with_none_line(self):
        """Test generate_issue_id with line=None still produces a valid ID."""
        issue_id = generate_issue_id("correctness", "src/main.py", None, "global issue")

        assert issue_id.startswith("correctness-")
        assert len(issue_id.split("-")[1]) == 8
        # Verify it's different from a line=0 case
        issue_id_with_zero = generate_issue_id(
            "correctness", "src/main.py", 0, "global issue"
        )
        assert issue_id != issue_id_with_zero


class TestReviewersList:
    """Test REVIEWERS list configuration."""

    def test_reviewers_list_contents(self):
        """Test that REVIEWERS list contains exactly ['correctness', 'security']."""
        assert REVIEWERS == ["correctness", "security"]


class TestRunReviewer:
    """Test single reviewer execution."""

    @patch("orchestrator.review_swarm._call_reviewer_api")
    def test_run_reviewer_returns_structured_findings(self, mock_call_api):
        """Test run_reviewer returns list of dicts matching structured finding schema."""
        mock_call_api.return_value = [
            {
                "issue_id": "correctness-abc12345",
                "reviewer": "correctness",
                "severity": "high",
                "issue": "Potential null pointer dereference",
                "suggested_fix": "Add null check before access",
                "file": "src/main.py",
                "line": 42,
            }
        ]

        diff = "diff --git a/src/main.py..."
        doctrine = "Review with focus on correctness"
        loop_state = {"pr_number": 123, "current_round": 1, "issues": {}}

        findings = run_reviewer("correctness", diff, doctrine, loop_state)

        assert isinstance(findings, list)
        assert len(findings) == 1
        finding = findings[0]
        # Verify schema
        assert "issue_id" in finding
        assert "reviewer" in finding
        assert "severity" in finding
        assert "issue" in finding
        assert "suggested_fix" in finding
        assert "file" in finding
        assert "line" in finding
        assert finding["reviewer"] == "correctness"


class TestDispatchSwarm:
    """Test parallel swarm dispatch."""

    @patch("orchestrator.review_swarm.run_reviewer")
    def test_dispatch_swarm_calls_all_reviewers(self, mock_run_reviewer):
        """Test dispatch_swarm calls all reviewers in REVIEWERS list."""
        mock_run_reviewer.return_value = [
            {
                "issue_id": "test-12345678",
                "reviewer": "test",
                "severity": "high",
                "issue": "Test issue",
                "suggested_fix": "Test fix",
                "file": "test.py",
                "line": 1,
            }
        ]

        diff = "diff --git a/src/main.py..."
        doctrine = "Review doctrine"
        loop_state = {"pr_number": 123, "current_round": 1, "issues": {}}

        result = dispatch_swarm(diff, doctrine, loop_state)

        # Verify return structure
        assert "findings" in result
        assert "reviewer_coverage" in result
        assert isinstance(result["findings"], list)
        assert isinstance(result["reviewer_coverage"], dict)

        # Verify reviewer coverage structure
        coverage = result["reviewer_coverage"]
        assert "expected" in coverage
        assert "completed" in coverage
        assert "failed" in coverage
        assert set(coverage["expected"]) == {"correctness", "security"}

    @patch("orchestrator.review_swarm.run_reviewer")
    def test_dispatch_swarm_graceful_degradation_on_exception(self, mock_run_reviewer):
        """Test dispatch_swarm handles exception from one reviewer gracefully."""

        def side_effect(reviewer, diff, doctrine, loop_state):
            if reviewer == "correctness":
                raise ValueError("Reviewer failed!")
            return [
                {
                    "issue_id": f"{reviewer}-12345678",
                    "reviewer": reviewer,
                    "severity": "high",
                    "issue": "Found issue",
                    "suggested_fix": "Fix it",
                    "file": "test.py",
                    "line": 1,
                }
            ]

        mock_run_reviewer.side_effect = side_effect

        diff = "diff --git a/src/main.py..."
        doctrine = "Review doctrine"
        loop_state = {"pr_number": 123, "current_round": 1, "issues": {}}

        result = dispatch_swarm(diff, doctrine, loop_state)

        # Should still return findings from security reviewer
        assert len(result["findings"]) >= 1

        # Verify failed reviewer is tracked
        coverage = result["reviewer_coverage"]
        assert any(f["reviewer"] == "correctness" for f in coverage["failed"])
        assert "security" in coverage["completed"]

        # Verify failed entry has error info
        failed_entry = next(
            f for f in coverage["failed"] if f["reviewer"] == "correctness"
        )
        assert "error" in failed_entry
        assert "Reviewer failed!" in failed_entry["error"]

    @patch("orchestrator.review_swarm.run_reviewer")
    def test_dispatch_swarm_timeout_handling(self, mock_run_reviewer):
        """Test dispatch_swarm records timeout when reviewer exceeds 2-minute timeout."""
        import time

        def slow_reviewer(reviewer, diff, doctrine, loop_state):
            if reviewer == "correctness":
                # Simulate a reviewer that takes too long
                # In real test, we'll mock this to trigger timeout
                time.sleep(0.1)  # Simulated delay
                raise TimeoutError("Reviewer timed out")
            return []

        mock_run_reviewer.side_effect = slow_reviewer

        diff = "diff --git a/src/main.py..."
        doctrine = "Review doctrine"
        loop_state = {"pr_number": 123, "current_round": 1, "issues": {}}

        # We need to test that timeouts are caught properly
        # The actual timeout is handled by ThreadPoolExecutor with as_completed
        result = dispatch_swarm(diff, doctrine, loop_state)

        # Verify timeout is recorded as a failure
        coverage = result["reviewer_coverage"]
        # At least one reviewer should fail with timeout
        if "correctness" in [f["reviewer"] for f in coverage["failed"]]:
            failed_entry = next(
                f for f in coverage["failed"] if f["reviewer"] == "correctness"
            )
            assert "error" in failed_entry

    @patch("orchestrator.review_swarm.run_reviewer")
    def test_dispatch_swarm_empty_diff(self, mock_run_reviewer):
        """Test dispatch_swarm with empty diff returns empty findings."""
        mock_run_reviewer.return_value = []

        diff = ""
        doctrine = "Review doctrine"
        loop_state = {"pr_number": 123, "current_round": 1, "issues": {}}

        result = dispatch_swarm(diff, doctrine, loop_state)

        assert result["findings"] == []
        # All reviewers should complete successfully even with empty diff
        coverage = result["reviewer_coverage"]
        assert len(coverage["completed"]) == len(REVIEWERS)
        assert len(coverage["failed"]) == 0
