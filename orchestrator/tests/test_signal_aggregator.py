"""
Tests for signal aggregation and normalization.

This module tests:
- load_swarm_findings: reading swarm results from JSON
- normalize_check_runs: converting PyGithub check runs to dicts
- classify_review_comment: categorizing reviews as AI vs human
- normalize_human_review: extracting review metadata
- parse_code_scanning_alerts: parsing security alerts from JSON
- parse_unresolved_threads: extracting unresolved GraphQL threads
- build_signals: assembling complete signals dict with de-duplication
- Graceful handling of missing/empty inputs
- Partial API failures with warnings
"""

import json
from unittest.mock import MagicMock, patch
from orchestrator.signal_aggregator import (
    load_swarm_findings,
    normalize_check_runs,
    classify_review_comment,
    normalize_human_review,
    parse_code_scanning_alerts,
    parse_unresolved_threads,
    build_signals,
)


class TestLoadSwarmFindings:
    """Test loading swarm findings from JSON file."""

    def test_load_swarm_findings_valid_file(self, tmp_path):
        """Test load_swarm_findings reads JSON and returns findings list and reviewer_coverage dict."""
        swarm_data = {
            "findings": [
                {
                    "issue_id": "correctness-abc12345",
                    "reviewer": "correctness",
                    "severity": "high",
                    "issue": "Null pointer dereference",
                    "suggested_fix": "Add null check",
                    "file": "src/main.py",
                    "line": 42,
                }
            ],
            "reviewer_coverage": {
                "expected": ["correctness", "security"],
                "completed": ["correctness", "security"],
                "failed": [],
            },
        }

        swarm_file = tmp_path / "swarm_result.json"
        swarm_file.write_text(json.dumps(swarm_data))

        findings, reviewer_coverage = load_swarm_findings(str(swarm_file))

        assert len(findings) == 1
        assert findings[0]["issue_id"] == "correctness-abc12345"
        assert reviewer_coverage["expected"] == ["correctness", "security"]
        assert reviewer_coverage["completed"] == ["correctness", "security"]
        assert reviewer_coverage["failed"] == []

    def test_load_swarm_findings_empty_findings(self, tmp_path):
        """Test load_swarm_findings with empty findings list."""
        swarm_data = {
            "findings": [],
            "reviewer_coverage": {
                "expected": ["correctness", "security"],
                "completed": ["correctness", "security"],
                "failed": [],
            },
        }

        swarm_file = tmp_path / "swarm_result.json"
        swarm_file.write_text(json.dumps(swarm_data))

        findings, reviewer_coverage = load_swarm_findings(str(swarm_file))

        assert findings == []
        assert reviewer_coverage["expected"] == ["correctness", "security"]


class TestNormalizeCheckRuns:
    """Test normalization of PyGithub check runs to dicts."""

    def test_normalize_check_runs_valid_checks(self):
        """Test normalize_check_runs converts check runs to dicts with required keys."""
        check1 = MagicMock()
        check1.name = "build"
        check1.conclusion = "success"
        check1.details_url = "https://github.com/org/repo/runs/123"
        check1.output = MagicMock()
        check1.output.summary = "Build passed"

        check2 = MagicMock()
        check2.name = "test"
        check2.conclusion = "failure"
        check2.details_url = "https://github.com/org/repo/runs/124"
        check2.output = MagicMock()
        check2.output.summary = "2 tests failed"

        result = normalize_check_runs([check1, check2])

        assert len(result) == 2
        assert result[0]["name"] == "build"
        assert result[0]["conclusion"] == "success"
        assert result[0]["details_url"] == "https://github.com/org/repo/runs/123"
        assert result[0]["output_summary"] == "Build passed"
        assert result[1]["name"] == "test"
        assert result[1]["conclusion"] == "failure"

    def test_normalize_check_runs_truncates_long_summary(self):
        """Test normalize_check_runs truncates output_summary to 2000 chars."""
        check = MagicMock()
        check.name = "test"
        check.conclusion = "failure"
        check.details_url = "https://github.com/org/repo/runs/123"
        check.output = MagicMock()
        check.output.summary = "x" * 3000  # 3000 chars

        result = normalize_check_runs([check])

        assert len(result[0]["output_summary"]) == 2000
        assert result[0]["output_summary"] == "x" * 2000

    def test_normalize_check_runs_empty_list(self):
        """Test normalize_check_runs with empty list returns empty list."""
        result = normalize_check_runs([])
        assert result == []

    def test_normalize_check_runs_none_output(self):
        """Test normalize_check_runs handles None output gracefully."""
        check = MagicMock()
        check.name = "build"
        check.conclusion = "success"
        check.details_url = "https://github.com/org/repo/runs/123"
        check.output = None

        result = normalize_check_runs([check])

        assert result[0]["output_summary"] == ""


class TestClassifyReviewComment:
    """Test classification of review comments as AI vs human."""

    def test_classify_review_comment_bot_user(self):
        """Test classify_review_comment returns 'ai_reviews' for Bot-type users."""
        comment = MagicMock()
        comment.user = MagicMock()
        comment.user.type = "Bot"
        comment.user.login = "github-actions[bot]"

        result = classify_review_comment(comment)
        assert result == "ai_reviews"

    def test_classify_review_comment_copilot_in_login(self):
        """Test classify_review_comment returns 'ai_reviews' for users with 'copilot' in login."""
        comment = MagicMock()
        comment.user = MagicMock()
        comment.user.type = "User"
        comment.user.login = "copilot-agent"

        result = classify_review_comment(comment)
        assert result == "ai_reviews"

    def test_classify_review_comment_copilot_case_insensitive(self):
        """Test classify_review_comment is case-insensitive for 'copilot' detection."""
        comment = MagicMock()
        comment.user = MagicMock()
        comment.user.type = "User"
        comment.user.login = "CoPiLoT-helper"

        result = classify_review_comment(comment)
        assert result == "ai_reviews"

    def test_classify_review_comment_human_user(self):
        """Test classify_review_comment returns 'human_reviews' for regular users."""
        comment = MagicMock()
        comment.user = MagicMock()
        comment.user.type = "User"
        comment.user.login = "john-doe"

        result = classify_review_comment(comment)
        assert result == "human_reviews"


class TestNormalizeHumanReview:
    """Test normalization of human review objects."""

    def test_normalize_human_review_extracts_fields(self):
        """Test normalize_human_review extracts id, state, author, body from review object."""
        review = MagicMock()
        review.id = 12345
        review.state = "APPROVED"
        review.user = MagicMock()
        review.user.login = "reviewer-1"
        review.body = "LGTM! Great work."

        result = normalize_human_review(review)

        assert result["id"] == 12345
        assert result["state"] == "APPROVED"
        assert result["author"] == "reviewer-1"
        assert result["body"] == "LGTM! Great work."

    def test_normalize_human_review_empty_body(self):
        """Test normalize_human_review handles empty body."""
        review = MagicMock()
        review.id = 12346
        review.state = "CHANGES_REQUESTED"
        review.user = MagicMock()
        review.user.login = "reviewer-2"
        review.body = ""

        result = normalize_human_review(review)

        assert result["body"] == ""


class TestParseCodeScanningAlerts:
    """Test parsing of code scanning alerts from JSON."""

    def test_parse_code_scanning_alerts_valid_json(self):
        """Test parse_code_scanning_alerts extracts rule, severity, path from alert JSON."""
        alerts_json = json.dumps(
            [
                {
                    "rule": {"id": "sql-injection", "severity": "critical"},
                    "most_recent_instance": {"location": {"path": "src/db.py"}},
                },
                {
                    "rule": {"id": "xss", "severity": "high"},
                    "most_recent_instance": {"location": {"path": "src/views.py"}},
                },
            ]
        )

        result = parse_code_scanning_alerts(alerts_json)

        assert len(result) == 2
        assert result[0]["rule"] == "sql-injection"
        assert result[0]["severity"] == "critical"
        assert result[0]["path"] == "src/db.py"
        assert result[1]["rule"] == "xss"
        assert result[1]["severity"] == "high"
        assert result[1]["path"] == "src/views.py"

    def test_parse_code_scanning_alerts_empty_input(self):
        """Test parse_code_scanning_alerts returns empty list for empty input."""
        result = parse_code_scanning_alerts("[]")
        assert result == []

    def test_parse_code_scanning_alerts_invalid_json(self):
        """Test parse_code_scanning_alerts returns empty list for invalid JSON."""
        result = parse_code_scanning_alerts("not valid json")
        assert result == []

    def test_parse_code_scanning_alerts_empty_string(self):
        """Test parse_code_scanning_alerts returns empty list for empty string."""
        result = parse_code_scanning_alerts("")
        assert result == []


class TestParseUnresolvedThreads:
    """Test parsing of unresolved GraphQL threads."""

    def test_parse_unresolved_threads_extracts_unresolved(self):
        """Test parse_unresolved_threads extracts unresolved threads with path, line, body, author."""
        graphql_json = json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [
                                    {
                                        "isResolved": False,
                                        "comments": {
                                            "nodes": [
                                                {
                                                    "path": "src/main.py",
                                                    "position": 42,
                                                    "body": "This needs to be fixed before merge.",
                                                    "author": {"login": "reviewer-1"},
                                                }
                                            ]
                                        },
                                    },
                                    {
                                        "isResolved": True,
                                        "comments": {
                                            "nodes": [
                                                {
                                                    "path": "src/test.py",
                                                    "position": 10,
                                                    "body": "Fixed now, thanks!",
                                                    "author": {"login": "reviewer-2"},
                                                }
                                            ]
                                        },
                                    },
                                ]
                            }
                        }
                    }
                }
            }
        )

        result = parse_unresolved_threads(graphql_json)

        assert len(result) == 1  # Only unresolved thread
        assert result[0]["path"] == "src/main.py"
        assert result[0]["line"] == 42
        assert result[0]["body"] == "This needs to be fixed before merge."
        assert result[0]["author"] == "reviewer-1"

    def test_parse_unresolved_threads_truncates_long_body(self):
        """Test parse_unresolved_threads truncates body to 500 chars."""
        graphql_json = json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [
                                    {
                                        "isResolved": False,
                                        "comments": {
                                            "nodes": [
                                                {
                                                    "path": "src/main.py",
                                                    "position": 42,
                                                    "body": "x" * 1000,  # 1000 chars
                                                    "author": {"login": "reviewer-1"},
                                                }
                                            ]
                                        },
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        )

        result = parse_unresolved_threads(graphql_json)

        assert len(result[0]["body"]) == 500
        assert result[0]["body"] == "x" * 500

    def test_parse_unresolved_threads_skips_resolved(self):
        """Test parse_unresolved_threads skips resolved threads."""
        graphql_json = json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [
                                    {
                                        "isResolved": True,
                                        "comments": {
                                            "nodes": [
                                                {
                                                    "path": "src/main.py",
                                                    "position": 42,
                                                    "body": "Resolved thread",
                                                    "author": {"login": "reviewer-1"},
                                                }
                                            ]
                                        },
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        )

        result = parse_unresolved_threads(graphql_json)
        assert result == []

    def test_parse_unresolved_threads_empty_input(self):
        """Test parse_unresolved_threads returns empty list for empty input."""
        result = parse_unresolved_threads("")
        assert result == []

    def test_parse_unresolved_threads_invalid_json(self):
        """Test parse_unresolved_threads returns empty list for invalid JSON."""
        result = parse_unresolved_threads("not valid json")
        assert result == []


class TestBuildSignals:
    """Test assembling complete signals dict."""

    def test_build_signals_assembles_all_keys(self, tmp_path):
        """Test build_signals assembles complete signals dict with all required keys."""
        # Setup swarm findings
        swarm_data = {
            "findings": [
                {
                    "issue_id": "correctness-abc12345",
                    "reviewer": "correctness",
                    "severity": "high",
                    "issue": "Null pointer",
                    "suggested_fix": "Add null check",
                    "file": "src/main.py",
                    "line": 42,
                }
            ],
            "reviewer_coverage": {
                "expected": ["correctness", "security"],
                "completed": ["correctness", "security"],
                "failed": [],
            },
        }
        swarm_file = tmp_path / "swarm_result.json"
        swarm_file.write_text(json.dumps(swarm_data))

        # Setup check runs
        check = MagicMock()
        check.name = "build"
        check.conclusion = "success"
        check.details_url = "https://github.com/org/repo/runs/123"
        check.output = MagicMock()
        check.output.summary = "Build passed"

        # Setup reviews
        review = MagicMock()
        review.id = 12345
        review.state = "APPROVED"
        review.user = MagicMock()
        review.user.login = "reviewer-1"
        review.user.type = "User"
        review.body = "LGTM"

        # Setup comments
        comment = MagicMock()
        comment.user = MagicMock()
        comment.user.type = "Bot"
        comment.user.login = "copilot[bot]"

        # Setup alerts
        alerts_json = json.dumps(
            [
                {
                    "rule": {"id": "sql-injection", "severity": "critical"},
                    "most_recent_instance": {"location": {"path": "src/db.py"}},
                }
            ]
        )

        # Setup threads
        threads_json = json.dumps(
            {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [
                                    {
                                        "isResolved": False,
                                        "comments": {
                                            "nodes": [
                                                {
                                                    "path": "src/main.py",
                                                    "position": 42,
                                                    "body": "Fix this",
                                                    "author": {"login": "reviewer-1"},
                                                }
                                            ]
                                        },
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        )

        result = build_signals(
            pr_number=123,
            head_sha="abc123def456",
            swarm_path=str(swarm_file),
            checks=[check],
            reviews=[review],
            comments=[comment],
            alerts_json=alerts_json,
            threads_json=threads_json,
        )

        assert result["pr_number"] == 123
        assert result["head_sha"] == "abc123def456"
        assert len(result["swarm_findings"]) == 1
        assert result["reviewer_coverage"]["expected"] == ["correctness", "security"]
        assert len(result["checks"]) == 1
        assert len(result["human_reviews"]) == 1
        assert len(result["ai_reviews"]) == 1
        assert len(result["unresolved_threads"]) == 1
        assert len(result["security"]) == 1

    def test_build_signals_deduplicates_findings(self, tmp_path):
        """Test build_signals de-duplicates findings with the same issue_id."""
        swarm_data = {
            "findings": [
                {
                    "issue_id": "correctness-abc12345",
                    "reviewer": "correctness",
                    "severity": "high",
                    "issue": "Null pointer",
                    "suggested_fix": "Add null check",
                    "file": "src/main.py",
                    "line": 42,
                },
                {
                    "issue_id": "correctness-abc12345",  # Duplicate
                    "reviewer": "correctness",
                    "severity": "high",
                    "issue": "Null pointer",
                    "suggested_fix": "Add null check",
                    "file": "src/main.py",
                    "line": 42,
                },
            ],
            "reviewer_coverage": {
                "expected": ["correctness", "security"],
                "completed": ["correctness", "security"],
                "failed": [],
            },
        }
        swarm_file = tmp_path / "swarm_result.json"
        swarm_file.write_text(json.dumps(swarm_data))

        result = build_signals(
            pr_number=123,
            head_sha="abc123",
            swarm_path=str(swarm_file),
            checks=[],
            reviews=[],
            comments=[],
            alerts_json="[]",
            threads_json="{}",
        )

        # Should have only 1 finding after de-duplication
        assert len(result["swarm_findings"]) == 1
        assert result["swarm_findings"][0]["issue_id"] == "correctness-abc12345"

    def test_build_signals_empty_inputs(self, tmp_path):
        """Test build_signals produces valid signals with empty lists for missing inputs."""
        swarm_data = {
            "findings": [],
            "reviewer_coverage": {
                "expected": ["correctness", "security"],
                "completed": ["correctness", "security"],
                "failed": [],
            },
        }
        swarm_file = tmp_path / "swarm_result.json"
        swarm_file.write_text(json.dumps(swarm_data))

        result = build_signals(
            pr_number=123,
            head_sha="abc123",
            swarm_path=str(swarm_file),
            checks=[],
            reviews=[],
            comments=[],
            alerts_json="[]",
            threads_json="{}",
        )

        assert result["pr_number"] == 123
        assert result["swarm_findings"] == []
        assert result["checks"] == []
        assert result["human_reviews"] == []
        assert result["ai_reviews"] == []
        assert result["unresolved_threads"] == []
        assert result["security"] == []

    @patch("orchestrator.signal_aggregator.parse_code_scanning_alerts")
    def test_build_signals_partial_api_failures(self, mock_parse_alerts, tmp_path):
        """Test build_signals still produces valid signals with warnings when API fails."""
        # Setup swarm findings
        swarm_data = {
            "findings": [
                {
                    "issue_id": "correctness-abc12345",
                    "reviewer": "correctness",
                    "severity": "high",
                    "issue": "Null pointer",
                    "suggested_fix": "Add null check",
                    "file": "src/main.py",
                    "line": 42,
                }
            ],
            "reviewer_coverage": {
                "expected": ["correctness", "security"],
                "completed": ["correctness", "security"],
                "failed": [],
            },
        }
        swarm_file = tmp_path / "swarm_result.json"
        swarm_file.write_text(json.dumps(swarm_data))

        # Simulate API failure by returning empty list
        mock_parse_alerts.return_value = []

        result = build_signals(
            pr_number=123,
            head_sha="abc123",
            swarm_path=str(swarm_file),
            checks=[],
            reviews=[],
            comments=[],
            alerts_json="",  # Empty/failed API response
            threads_json="{}",
        )

        # Should still have valid structure
        assert result["pr_number"] == 123
        assert len(result["swarm_findings"]) == 1
        assert result["security"] == []  # Empty due to API failure
