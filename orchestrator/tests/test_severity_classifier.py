"""
Tests for severity classification and filtering logic.

This module tests:
- SEV_ORDER mapping (critical=4, high=3, medium=2, low=1, info=0)
- severity_gte and severity_lt comparison helpers
- filter_by_floor threshold filtering
- classify_severity normalization
"""

from orchestrator.severity_classifier import (
    SEV_ORDER,
    severity_gte,
    severity_lt,
    filter_by_floor,
    classify_severity,
)
from orchestrator.models import Issue


class TestSeverityOrder:
    """Test the SEV_ORDER mapping."""

    def test_sev_order_mapping(self):
        """Test SEV_ORDER dict maps critical=4, high=3, medium=2, low=1, info=0."""
        assert SEV_ORDER == {
            "critical": 4,
            "high": 3,
            "medium": 2,
            "low": 1,
            "info": 0,
        }


class TestSeverityComparison:
    """Test severity comparison helpers."""

    def test_severity_gte_critical_vs_high(self):
        """Test severity_gte returns True when critical >= high."""
        assert severity_gte("critical", "high") is True

    def test_severity_gte_low_vs_high(self):
        """Test severity_gte returns False when low >= high."""
        assert severity_gte("low", "high") is False

    def test_severity_gte_equal_severities(self):
        """Test severity_gte returns True when severities are equal."""
        assert severity_gte("high", "high") is True
        assert severity_gte("medium", "medium") is True
        assert severity_gte("low", "low") is True

    def test_severity_gte_all_vs_info(self):
        """Test that all severities are >= info."""
        for sev in ["critical", "high", "medium", "low", "info"]:
            assert severity_gte(sev, "info") is True

    def test_severity_lt_high_vs_critical(self):
        """Test severity_lt returns True when high < critical."""
        assert severity_lt("high", "critical") is True

    def test_severity_lt_high_vs_low(self):
        """Test severity_lt returns False when high < low."""
        assert severity_lt("high", "low") is False

    def test_severity_lt_equal_severities(self):
        """Test severity_lt returns False when severities are equal."""
        assert severity_lt("high", "high") is False
        assert severity_lt("medium", "medium") is False


class TestFilterByFloor:
    """Test filter_by_floor threshold filtering."""

    def test_filter_by_floor_high_threshold(self):
        """Test filter_by_floor with floor='high' keeps critical+high, discards medium+low+info."""
        issues = {
            "issue-1": Issue(
                issue_id="issue-1",
                reviewer="correctness",
                severity="critical",
                issue="Critical bug",
                suggested_fix="Fix it",
                file="test.py",
                line=10,
                found_in_round=1,
            ),
            "issue-2": Issue(
                issue_id="issue-2",
                reviewer="correctness",
                severity="high",
                issue="High severity",
                suggested_fix="Fix it",
                file="test.py",
                line=20,
                found_in_round=1,
            ),
            "issue-3": Issue(
                issue_id="issue-3",
                reviewer="security",
                severity="medium",
                issue="Medium issue",
                suggested_fix="Fix it",
                file="test.py",
                line=30,
                found_in_round=1,
            ),
            "issue-4": Issue(
                issue_id="issue-4",
                reviewer="security",
                severity="low",
                issue="Low issue",
                suggested_fix="Fix it",
                file="test.py",
                line=40,
                found_in_round=1,
            ),
            "issue-5": Issue(
                issue_id="issue-5",
                reviewer="correctness",
                severity="info",
                issue="Info only",
                suggested_fix="N/A",
                file="test.py",
                line=50,
                found_in_round=1,
            ),
        }

        filtered = filter_by_floor(issues, "high")
        assert len(filtered) == 2
        assert "issue-1" in filtered
        assert "issue-2" in filtered
        assert "issue-3" not in filtered
        assert "issue-4" not in filtered
        assert "issue-5" not in filtered

    def test_filter_by_floor_info_keeps_all(self):
        """Test filter_by_floor with floor='info' keeps all issues."""
        issues = {
            "issue-1": Issue(
                issue_id="issue-1",
                reviewer="correctness",
                severity="critical",
                issue="Critical",
                suggested_fix="Fix",
                file="test.py",
                line=10,
                found_in_round=1,
            ),
            "issue-2": Issue(
                issue_id="issue-2",
                reviewer="correctness",
                severity="info",
                issue="Info",
                suggested_fix="N/A",
                file="test.py",
                line=20,
                found_in_round=1,
            ),
        }

        filtered = filter_by_floor(issues, "info")
        assert len(filtered) == 2
        assert "issue-1" in filtered
        assert "issue-2" in filtered

    def test_filter_by_floor_critical_only(self):
        """Test filter_by_floor with floor='critical' keeps only critical issues."""
        issues = {
            "issue-1": Issue(
                issue_id="issue-1",
                reviewer="correctness",
                severity="critical",
                issue="Critical bug",
                suggested_fix="Fix it",
                file="test.py",
                line=10,
                found_in_round=1,
            ),
            "issue-2": Issue(
                issue_id="issue-2",
                reviewer="security",
                severity="high",
                issue="High severity",
                suggested_fix="Fix it",
                file="test.py",
                line=20,
                found_in_round=1,
            ),
        }

        filtered = filter_by_floor(issues, "critical")
        assert len(filtered) == 1
        assert "issue-1" in filtered
        assert "issue-2" not in filtered

    def test_filter_by_floor_empty_issues(self):
        """Test filter_by_floor with empty issues list returns empty dict."""
        issues = {}
        filtered = filter_by_floor(issues, "high")
        assert filtered == {}
        assert isinstance(filtered, dict)

    def test_filter_by_floor_preserves_insertion_order(self):
        """Test filter_by_floor preserves stable issue ordering (insertion-order dict behavior)."""
        issues = {
            "issue-3": Issue(
                issue_id="issue-3",
                reviewer="correctness",
                severity="high",
                issue="Third",
                suggested_fix="Fix",
                file="test.py",
                line=30,
                found_in_round=1,
            ),
            "issue-1": Issue(
                issue_id="issue-1",
                reviewer="correctness",
                severity="critical",
                issue="First",
                suggested_fix="Fix",
                file="test.py",
                line=10,
                found_in_round=1,
            ),
            "issue-2": Issue(
                issue_id="issue-2",
                reviewer="security",
                severity="high",
                issue="Second",
                suggested_fix="Fix",
                file="test.py",
                line=20,
                found_in_round=1,
            ),
            "issue-4": Issue(
                issue_id="issue-4",
                reviewer="security",
                severity="medium",
                issue="Fourth (filtered out)",
                suggested_fix="Fix",
                file="test.py",
                line=40,
                found_in_round=1,
            ),
        }

        filtered = filter_by_floor(issues, "high")
        keys = list(filtered.keys())
        # Should preserve the order: issue-3, issue-1, issue-2 (same order as input)
        assert keys == ["issue-3", "issue-1", "issue-2"]


class TestClassifySeverity:
    """Test classify_severity normalization."""

    def test_classify_severity_uppercase_high(self):
        """Test classify_severity normalizes 'HIGH' to 'high'."""
        assert classify_severity("HIGH") == "high"

    def test_classify_severity_uppercase_critical(self):
        """Test classify_severity normalizes 'CRITICAL' to 'critical'."""
        assert classify_severity("CRITICAL") == "critical"

    def test_classify_severity_mixed_case(self):
        """Test classify_severity normalizes mixed case."""
        assert classify_severity("MeDiUm") == "medium"
        assert classify_severity("Low") == "low"
        assert classify_severity("Info") == "info"

    def test_classify_severity_already_lowercase(self):
        """Test classify_severity handles already lowercase strings."""
        assert classify_severity("critical") == "critical"
        assert classify_severity("high") == "high"
        assert classify_severity("medium") == "medium"
        assert classify_severity("low") == "low"
        assert classify_severity("info") == "info"

    def test_classify_severity_unknown_defaults_to_info(self):
        """Test classify_severity returns 'info' for unknown values."""
        assert classify_severity("unknown") == "info"
        assert classify_severity("invalid") == "info"
        assert classify_severity("") == "info"
        assert classify_severity("foobar") == "info"
        assert classify_severity("severe") == "info"
