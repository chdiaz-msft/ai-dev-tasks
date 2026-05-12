"""
Tests for orchestrator.models data structures.

Tests cover:
- Issue creation with all fields and correct defaults
- LoopState creation with all fields and correct defaults
- TerminationStatus enum has exactly four values
- Issue.status only accepts valid values
- LoopState serialization/deserialization round-trips correctly
- Issue serialization/deserialization round-trips correctly
"""

from orchestrator.models import Issue, LoopState, TerminationStatus, IssueStatus


class TestIssue:
    """Tests for the Issue dataclass."""

    def test_issue_creation_with_all_fields(self):
        """Test Issue creation with all fields provided."""
        issue = Issue(
            issue_id="correctness-abc12345",
            reviewer="correctness",
            severity="high",
            issue="Potential null pointer dereference",
            suggested_fix="Add null check before accessing property",
            file="src/main.py",
            line=42,
            found_in_round=1,
            status="open",
            resolved_in_round=None,
            resolution=None,
        )

        assert issue.issue_id == "correctness-abc12345"
        assert issue.reviewer == "correctness"
        assert issue.severity == "high"
        assert issue.issue == "Potential null pointer dereference"
        assert issue.suggested_fix == "Add null check before accessing property"
        assert issue.file == "src/main.py"
        assert issue.line == 42
        assert issue.found_in_round == 1
        assert issue.status == "open"
        assert issue.resolved_in_round is None
        assert issue.resolution is None

    def test_issue_defaults(self):
        """Test Issue creation with default values for optional fields."""
        issue = Issue(
            issue_id="security-def67890",
            reviewer="security",
            severity="critical",
            issue="SQL injection vulnerability",
            suggested_fix="Use parameterized queries",
            file="src/db.py",
            line=100,
            found_in_round=2,
        )

        # Check that defaults are applied correctly
        assert issue.status == "open"
        assert issue.resolved_in_round is None
        assert issue.resolution is None

    def test_issue_with_line_none(self):
        """Test Issue creation with line=None (for file-level issues)."""
        issue = Issue(
            issue_id="correctness-xyz99999",
            reviewer="correctness",
            severity="medium",
            issue="Missing error handling",
            suggested_fix="Add try-catch block",
            file="src/handler.py",
            line=None,
            found_in_round=1,
        )

        assert issue.line is None

    def test_issue_status_valid_values(self):
        """Test that Issue.status accepts all valid values."""
        valid_statuses = ["open", "resolved", "wont_fix", "escalated"]

        for status in valid_statuses:
            issue = Issue(
                issue_id=f"test-{status}",
                reviewer="correctness",
                severity="low",
                issue="Test issue",
                suggested_fix="Fix it",
                file="test.py",
                line=1,
                found_in_round=1,
                status=status,
            )
            assert issue.status == status

    def test_issue_to_dict(self):
        """Test Issue.to_dict() serialization."""
        issue = Issue(
            issue_id="correctness-abc12345",
            reviewer="correctness",
            severity="high",
            issue="Potential null pointer dereference",
            suggested_fix="Add null check",
            file="src/main.py",
            line=42,
            found_in_round=1,
            status="resolved",
            resolved_in_round=3,
            resolution="Fixed by adding null check",
        )

        issue_dict = issue.to_dict()

        assert isinstance(issue_dict, dict)
        assert issue_dict["issue_id"] == "correctness-abc12345"
        assert issue_dict["reviewer"] == "correctness"
        assert issue_dict["severity"] == "high"
        assert issue_dict["issue"] == "Potential null pointer dereference"
        assert issue_dict["suggested_fix"] == "Add null check"
        assert issue_dict["file"] == "src/main.py"
        assert issue_dict["line"] == 42
        assert issue_dict["found_in_round"] == 1
        assert issue_dict["status"] == "resolved"
        assert issue_dict["resolved_in_round"] == 3
        assert issue_dict["resolution"] == "Fixed by adding null check"

    def test_issue_from_dict(self):
        """Test Issue.from_dict() deserialization."""
        issue_dict = {
            "issue_id": "security-def67890",
            "reviewer": "security",
            "severity": "critical",
            "issue": "SQL injection vulnerability",
            "suggested_fix": "Use parameterized queries",
            "file": "src/db.py",
            "line": 100,
            "found_in_round": 2,
            "status": "open",
            "resolved_in_round": None,
            "resolution": None,
        }

        issue = Issue.from_dict(issue_dict)

        assert issue.issue_id == "security-def67890"
        assert issue.reviewer == "security"
        assert issue.severity == "critical"
        assert issue.issue == "SQL injection vulnerability"
        assert issue.suggested_fix == "Use parameterized queries"
        assert issue.file == "src/db.py"
        assert issue.line == 100
        assert issue.found_in_round == 2
        assert issue.status == "open"
        assert issue.resolved_in_round is None
        assert issue.resolution is None

    def test_issue_roundtrip_serialization(self):
        """Test that Issue.to_dict() round-trips correctly through Issue.from_dict()."""
        original_issue = Issue(
            issue_id="correctness-abc12345",
            reviewer="correctness",
            severity="high",
            issue="Potential null pointer dereference",
            suggested_fix="Add null check",
            file="src/main.py",
            line=42,
            found_in_round=1,
            status="resolved",
            resolved_in_round=3,
            resolution="Fixed by adding null check",
        )

        issue_dict = original_issue.to_dict()
        restored_issue = Issue.from_dict(issue_dict)

        assert restored_issue.issue_id == original_issue.issue_id
        assert restored_issue.reviewer == original_issue.reviewer
        assert restored_issue.severity == original_issue.severity
        assert restored_issue.issue == original_issue.issue
        assert restored_issue.suggested_fix == original_issue.suggested_fix
        assert restored_issue.file == original_issue.file
        assert restored_issue.line == original_issue.line
        assert restored_issue.found_in_round == original_issue.found_in_round
        assert restored_issue.status == original_issue.status
        assert restored_issue.resolved_in_round == original_issue.resolved_in_round
        assert restored_issue.resolution == original_issue.resolution


class TestLoopState:
    """Tests for the LoopState dataclass."""

    def test_loopstate_creation_with_all_fields(self):
        """Test LoopState creation with all fields provided."""
        issues = {
            "correctness-abc12345": Issue(
                issue_id="correctness-abc12345",
                reviewer="correctness",
                severity="high",
                issue="Test issue",
                suggested_fix="Fix it",
                file="test.py",
                line=1,
                found_in_round=1,
            )
        }

        state = LoopState(
            pr_number=123,
            current_round=3,
            max_rounds=5,
            issues=issues,
            termination="success",
            last_round_timestamp="2026-05-12T10:30:00Z",
        )

        assert state.pr_number == 123
        assert state.current_round == 3
        assert state.max_rounds == 5
        assert len(state.issues) == 1
        assert "correctness-abc12345" in state.issues
        assert state.termination == "success"
        assert state.last_round_timestamp == "2026-05-12T10:30:00Z"

    def test_loopstate_defaults(self):
        """Test LoopState creation with default values for optional fields."""
        state = LoopState(pr_number=456)

        # Check that defaults are applied correctly
        assert state.current_round == 0
        assert state.max_rounds == 5
        assert state.issues == {}
        assert state.termination is None
        assert state.last_round_timestamp is None

    def test_loopstate_to_dict(self):
        """Test LoopState.to_dict() serialization."""
        issues = {
            "correctness-abc12345": Issue(
                issue_id="correctness-abc12345",
                reviewer="correctness",
                severity="high",
                issue="Test issue",
                suggested_fix="Fix it",
                file="test.py",
                line=1,
                found_in_round=1,
            )
        }

        state = LoopState(
            pr_number=123,
            current_round=3,
            max_rounds=5,
            issues=issues,
            termination="handoff",
            last_round_timestamp="2026-05-12T10:30:00Z",
        )

        state_dict = state.to_dict()

        assert isinstance(state_dict, dict)
        assert state_dict["pr_number"] == 123
        assert state_dict["current_round"] == 3
        assert state_dict["max_rounds"] == 5
        assert isinstance(state_dict["issues"], dict)
        assert "correctness-abc12345" in state_dict["issues"]
        assert isinstance(state_dict["issues"]["correctness-abc12345"], dict)
        assert state_dict["termination"] == "handoff"
        assert state_dict["last_round_timestamp"] == "2026-05-12T10:30:00Z"

    def test_loopstate_from_dict(self):
        """Test LoopState.from_dict() deserialization."""
        state_dict = {
            "pr_number": 789,
            "current_round": 2,
            "max_rounds": 10,
            "issues": {
                "security-def67890": {
                    "issue_id": "security-def67890",
                    "reviewer": "security",
                    "severity": "critical",
                    "issue": "SQL injection",
                    "suggested_fix": "Use params",
                    "file": "db.py",
                    "line": 50,
                    "found_in_round": 1,
                    "status": "open",
                    "resolved_in_round": None,
                    "resolution": None,
                }
            },
            "termination": None,
            "last_round_timestamp": "2026-05-12T11:00:00Z",
        }

        state = LoopState.from_dict(state_dict)

        assert state.pr_number == 789
        assert state.current_round == 2
        assert state.max_rounds == 10
        assert len(state.issues) == 1
        assert "security-def67890" in state.issues
        assert isinstance(state.issues["security-def67890"], Issue)
        assert state.issues["security-def67890"].issue_id == "security-def67890"
        assert state.termination is None
        assert state.last_round_timestamp == "2026-05-12T11:00:00Z"

    def test_loopstate_roundtrip_serialization(self):
        """Test that LoopState.to_dict() round-trips correctly through LoopState.from_dict()."""
        issues = {
            "correctness-abc12345": Issue(
                issue_id="correctness-abc12345",
                reviewer="correctness",
                severity="high",
                issue="Potential null pointer dereference",
                suggested_fix="Add null check",
                file="src/main.py",
                line=42,
                found_in_round=1,
                status="resolved",
                resolved_in_round=3,
                resolution="Fixed",
            ),
            "security-def67890": Issue(
                issue_id="security-def67890",
                reviewer="security",
                severity="critical",
                issue="SQL injection vulnerability",
                suggested_fix="Use parameterized queries",
                file="src/db.py",
                line=100,
                found_in_round=2,
            ),
        }

        original_state = LoopState(
            pr_number=123,
            current_round=3,
            max_rounds=5,
            issues=issues,
            termination="blocked",
            last_round_timestamp="2026-05-12T10:30:00Z",
        )

        state_dict = original_state.to_dict()
        restored_state = LoopState.from_dict(state_dict)

        assert restored_state.pr_number == original_state.pr_number
        assert restored_state.current_round == original_state.current_round
        assert restored_state.max_rounds == original_state.max_rounds
        assert len(restored_state.issues) == len(original_state.issues)
        assert restored_state.termination == original_state.termination
        assert (
            restored_state.last_round_timestamp == original_state.last_round_timestamp
        )

        # Verify issues are correctly restored
        for issue_id, original_issue in original_state.issues.items():
            assert issue_id in restored_state.issues
            restored_issue = restored_state.issues[issue_id]
            assert restored_issue.issue_id == original_issue.issue_id
            assert restored_issue.reviewer == original_issue.reviewer
            assert restored_issue.severity == original_issue.severity
            assert restored_issue.status == original_issue.status


class TestTerminationStatus:
    """Tests for the TerminationStatus enum."""

    def test_termination_status_has_four_values(self):
        """Test that TerminationStatus enum has exactly four values."""
        expected_values = {"SUCCESS", "HANDOFF", "BLOCKED", "WAITING"}
        actual_values = {member.name for member in TerminationStatus}

        assert len(actual_values) == 4
        assert actual_values == expected_values

    def test_termination_status_values(self):
        """Test that TerminationStatus enum values are correct."""
        assert TerminationStatus.SUCCESS.value == "success"
        assert TerminationStatus.HANDOFF.value == "handoff"
        assert TerminationStatus.BLOCKED.value == "blocked"
        assert TerminationStatus.WAITING.value == "waiting"


class TestIssueStatus:
    """Tests for the IssueStatus enum."""

    def test_issue_status_has_five_values(self):
        """Test that IssueStatus enum has exactly five values."""
        expected_values = {"OPEN", "RESOLVED", "WONT_FIX", "ESCALATED", "ABANDONED"}
        actual_values = {member.name for member in IssueStatus}

        assert len(actual_values) == 5
        assert actual_values == expected_values

    def test_issue_status_values(self):
        """Test that IssueStatus enum values are correct."""
        assert IssueStatus.OPEN.value == "open"
        assert IssueStatus.RESOLVED.value == "resolved"
        assert IssueStatus.WONT_FIX.value == "wont_fix"
        assert IssueStatus.ESCALATED.value == "escalated"
        assert IssueStatus.ABANDONED.value == "abandoned"
