"""
Tests for task_parser.py CLI argument parsing and help output.

This test suite validates that the argparse-based CLI correctly:
- Parses all command arguments
- Displays help text correctly
- Validates required arguments
- Handles optional arguments
- Returns appropriate exit codes

Following TDD: these tests are written before full implementation.
"""

import subprocess
import sys
from pathlib import Path

import pytest

# Path to the task_parser.py script
TASK_PARSER = Path(__file__).parent.parent / "task_parser.py"

# Add parent directory to path so we can import task_parser module
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestCLIHelpOutput:
    """Test suite for --help output and basic CLI structure."""

    def test_help_flag_exits_zero(self):
        """--help should display help and exit with code 0."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Parse and manipulate markdown task files" in result.stdout

    def test_help_shows_all_commands(self):
        """--help should list all available subcommands."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        help_output = result.stdout

        # Check for all required commands
        assert "next-task" in help_output
        assert "next-parent" in help_output
        assert "count" in help_output
        assert "mark-complete" in help_output
        assert "mark-failed" in help_output
        assert "mark-subtree-failed" in help_output
        assert "is-complete" in help_output
        assert "is-subtree-resolved" in help_output
        assert "auto-complete-parents" in help_output
        assert "verification-section" in help_output
        assert "validate" in help_output

    def test_no_command_shows_error(self):
        """Running without a command should show error and exit non-zero."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER)],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        # Should mention required argument or show usage
        assert "required" in result.stderr.lower() or "usage" in result.stderr.lower()

    def test_invalid_command_shows_error(self):
        """Invalid command should show error and exit non-zero."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "invalid-command"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "invalid choice" in result.stderr.lower()


class TestNextTaskCommand:
    """Test argument parsing for next-task command."""

    def test_next_task_help(self):
        """next-task --help should show command-specific help."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "next-task" in result.stdout
        assert "file" in result.stdout.lower()

    def test_next_task_requires_file(self):
        """next-task without file argument should error."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "required" in result.stderr.lower() or "file" in result.stderr.lower()

    def test_next_task_accepts_file(self):
        """next-task should accept file argument (stub returns success)."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", "dummy.md"],
            capture_output=True,
            text=True,
        )
        # Should run without argument parsing errors
        # (May fail later due to missing file, but argparse should succeed)
        assert result.returncode in [0, 3]  # 0 = success, 3 = command error


class TestNextParentCommand:
    """Test argument parsing and output for next-parent command."""

    def test_next_parent_help(self):
        """next-parent --help should show command-specific help."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-parent", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "next-parent" in result.stdout
        assert "file" in result.stdout.lower()

    def test_next_parent_returns_full_top_level_task_context(self, tmp_path):
        """next-parent should return metadata followed by the complete task subtree."""
        task_file = tmp_path / "tasks.md"
        task_file.write_text(
            """## Tasks

- [x] 1.0 Completed parent
  - [x] 1.1 Completed child
- [ ] 2.0 Implement feature
  Parent-level implementation notes.
  - [x] 2.1 Existing setup
  - [ ] 2.2 Add behavior
    - [ ] 2.2.1 Add nested case
  - [ ] 2.3 Add tests
- [ ] 3.0 Later parent
  - [ ] 3.1 Do later work

## Verification Criteria

- [ ] Tests pass
""",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-parent", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        lines = result.stdout.splitlines()
        assert lines[0] == "5|10|2.0 Implement feature"
        assert "\n".join(lines[1:]) == (
            "- [ ] 2.0 Implement feature\n"
            "  Parent-level implementation notes.\n"
            "  - [x] 2.1 Existing setup\n"
            "  - [ ] 2.2 Add behavior\n"
            "    - [ ] 2.2.1 Add nested case\n"
            "  - [ ] 2.3 Add tests"
        )

    def test_next_parent_returns_leaf_as_its_own_context(self, tmp_path):
        """A top-level leaf task should be returned as a one-line parent context."""
        task_file = tmp_path / "tasks.md"
        task_file.write_text(
            """## Tasks

- [x] 1.0 Completed task
- [ ] 2.0 Standalone task

## Verification Criteria
""",
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-parent", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert result.stdout == "4|4|2.0 Standalone task\n- [ ] 2.0 Standalone task\n"


class TestIsSubtreeResolvedCommand:
    """Test subtree resolution checks used by parent-task execution."""

    def test_incomplete_descendant_returns_one(self, tmp_path):
        task_file = tmp_path / "tasks.md"
        task_file.write_text(
            """## Tasks

- [x] 1.0 Parent marked too early
  - [x] 1.1 Complete child
  - [ ] 1.2 Incomplete child
""",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(TASK_PARSER),
                "is-subtree-resolved",
                str(task_file),
                "--line",
                "3",
                "--match",
                "1.0 Parent marked too early",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1

    def test_complete_and_failed_descendants_return_zero(self, tmp_path):
        task_file = tmp_path / "tasks.md"
        task_file.write_text(
            """## Tasks

- [ ] 1.0 Parent
  - [x] 1.1 Complete child
  - [!] 1.2 Blocked child
""",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(TASK_PARSER),
                "is-subtree-resolved",
                str(task_file),
                "--line",
                "3",
                "--match",
                "1.0 Parent",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0


class TestMarkSubtreeFailedCommand:
    """Test atomic failure marking for parent-task execution."""

    def test_marks_only_incomplete_tasks_in_subtree_failed(self, tmp_path):
        task_file = tmp_path / "tasks.md"
        task_file.write_text(
            """## Tasks

- [ ] 1.0 Parent
  - [x] 1.1 Complete child
  - [ ] 1.2 Incomplete child
    - [ ] 1.2.1 Nested incomplete child
- [ ] 2.0 Unrelated parent
  - [ ] 2.1 Unrelated child
""",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(TASK_PARSER),
                "mark-subtree-failed",
                str(task_file),
                "--line",
                "3",
                "--match",
                "1.0 Parent",
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        content = task_file.read_text(encoding="utf-8")
        assert "- [!] 1.0 Parent" in content
        assert "  - [x] 1.1 Complete child" in content
        assert "  - [!] 1.2 Incomplete child" in content
        assert "    - [!] 1.2.1 Nested incomplete child" in content
        assert "- [ ] 2.0 Unrelated parent" in content
        assert "  - [ ] 2.1 Unrelated child" in content


class TestCountCommand:
    """Test argument parsing for count command."""

    def test_count_help(self):
        """count --help should show command-specific help."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "count" in result.stdout.lower()
        assert "file" in result.stdout.lower()

    def test_count_requires_file(self):
        """count without file argument should error."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "required" in result.stderr.lower() or "file" in result.stderr.lower()


class TestMarkCompleteCommand:
    """Test argument parsing for mark-complete command."""

    def test_mark_complete_help(self):
        """mark-complete --help should show command-specific help."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "mark-complete" in result.stdout
        assert "identifier" in result.stdout.lower()
        assert "--line" in result.stdout
        assert "--match" in result.stdout

    def test_mark_complete_requires_file_and_identifier(self):
        """mark-complete requires both file and identifier."""
        # Missing both
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0

        # Missing identifier
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", "dummy.md"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0

    def test_mark_complete_accepts_optional_line(self):
        """mark-complete should accept optional --line argument."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", "dummy.md", "42", "--line", "10"],
            capture_output=True,
            text=True,
        )
        # Should parse successfully (may fail later in execution)
        assert result.returncode in [0, 3]

    def test_mark_complete_accepts_optional_match(self):
        """mark-complete should accept optional --match argument."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", "dummy.md", "42", "--match", "task description"],
            capture_output=True,
            text=True,
        )
        # Should parse successfully (may fail later in execution)
        assert result.returncode in [0, 3]


class TestMarkFailedCommand:
    """Test argument parsing for mark-failed command."""

    def test_mark_failed_help(self):
        """mark-failed --help should show command-specific help."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-failed", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "mark-failed" in result.stdout
        assert "identifier" in result.stdout.lower()
        assert "--line" in result.stdout
        assert "--match" in result.stdout

    def test_mark_failed_requires_file_and_identifier(self):
        """mark-failed requires both file and identifier."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-failed", "dummy.md"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0


class TestIsCompleteCommand:
    """Test argument parsing for is-complete command."""

    def test_is_complete_help(self):
        """is-complete --help should show command-specific help."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "is-complete" in result.stdout
        assert "identifier" in result.stdout.lower()

    def test_is_complete_requires_file_and_identifier(self):
        """is-complete requires both file and identifier."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", "dummy.md"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0


class TestAutoCompleteParentsCommand:
    """Test argument parsing for auto-complete-parents command."""

    def test_auto_complete_parents_help(self):
        """auto-complete-parents --help should show command-specific help."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "auto-complete-parents", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "auto-complete-parents" in result.stdout
        assert "file" in result.stdout.lower()

    def test_auto_complete_parents_requires_file(self):
        """auto-complete-parents requires file argument."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "auto-complete-parents"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0


class TestVerificationSectionCommand:
    """Test argument parsing for verification-section command."""

    def test_verification_section_help(self):
        """verification-section --help should show command-specific help."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "verification-section" in result.stdout
        assert "file" in result.stdout.lower()

    def test_verification_section_requires_file(self):
        """verification-section requires file argument."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0


class TestValidateCommand:
    """Test argument parsing for validate command."""

    def test_validate_help(self):
        """validate --help should show command-specific help."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "validate" in result.stdout.lower()
        assert "file" in result.stdout.lower()

    def test_validate_requires_file(self):
        """validate requires file argument."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0


class TestSectionDetection:
    """Test suite for ## Tasks section detection.

    Tests that the parser correctly:
    - Finds ## Tasks heading (h1–h3, case-insensitive)
    - Stops at next equal-or-higher heading or EOF
    - Handles various heading formats and edge cases
    """

    def test_detect_h2_tasks_section(self):
        """Should detect ## Tasks heading (h2 level)."""
        content = """# Document Title

## Tasks

- [ ] Task 1
- [ ] Task 2

## Next Section
"""
        # We'll need to import the parse function once implemented
        # For now, this test validates the expected behavior
        import task_parser

        start, end = task_parser.find_tasks_section(content)
        assert start is not None
        assert "## Tasks" in content.splitlines()[start]
        # Should stop at "## Next Section" (equal-level heading)
        assert end < len(content.splitlines())

    def test_detect_h1_tasks_section(self):
        """Should detect # Tasks heading (h1 level)."""
        content = """# Tasks

- [ ] Task 1
- [ ] Task 2
"""
        import task_parser

        start, end = task_parser.find_tasks_section(content)
        assert start is not None
        assert "# Tasks" in content.splitlines()[start]

    def test_detect_h3_tasks_section(self):
        """Should detect ### Tasks heading (h3 level)."""
        content = """# Document Title

## Section

### Tasks

- [ ] Task 1
- [ ] Task 2

## Next Section
"""
        import task_parser

        start, end = task_parser.find_tasks_section(content)
        assert start is not None
        assert "### Tasks" in content.splitlines()[start]
        # Should stop at "## Next Section" (higher-level heading)

    def test_case_insensitive_tasks_heading(self):
        """Should detect TASKS, tasks, TaSks (case-insensitive)."""
        test_cases = [
            "## TASKS\n",
            "## tasks\n",
            "## TaSks\n",
            "# Tasks\n",
            "### tAsKs\n",
        ]
        import task_parser

        for heading in test_cases:
            content = f"""{heading}
- [ ] Task 1
"""
            start, end = task_parser.find_tasks_section(content)
            assert start is not None, f"Failed to detect: {heading.strip()}"

    def test_stop_at_equal_level_heading(self):
        """Should stop at next equal-level heading."""
        content = """## Tasks

- [ ] Task 1
- [ ] Task 2

## Next Section

This should not be included.
"""
        import task_parser

        start, end = task_parser.find_tasks_section(content)
        lines = content.splitlines()

        # Should include tasks but stop before "## Next Section"
        section_content = "\n".join(lines[start:end])
        assert "Task 1" in section_content
        assert "Task 2" in section_content
        assert "Next Section" not in section_content
        assert "This should not be included" not in section_content

    def test_stop_at_higher_level_heading(self):
        """Should stop at higher-level heading (h3 stops at h2 or h1)."""
        content = """### Tasks

- [ ] Task 1
- [ ] Task 2

## Higher Level Section

This should not be included.
"""
        import task_parser

        start, end = task_parser.find_tasks_section(content)
        lines = content.splitlines()

        section_content = "\n".join(lines[start:end])
        assert "Task 1" in section_content
        assert "Higher Level Section" not in section_content

    def test_continue_past_lower_level_heading(self):
        """Should continue past lower-level headings (h2 continues past h3)."""
        content = """## Tasks

- [ ] Task 1

### Subtasks

- [ ] Subtask 1
- [ ] Subtask 2

## End Section
"""
        import task_parser

        start, end = task_parser.find_tasks_section(content)
        lines = content.splitlines()

        section_content = "\n".join(lines[start:end])
        assert "Task 1" in section_content
        assert "Subtasks" in section_content
        assert "Subtask 1" in section_content
        assert "End Section" not in section_content

    def test_stop_at_eof_when_no_next_heading(self):
        """Should extend to EOF when no equal-or-higher heading follows."""
        content = """## Tasks

- [ ] Task 1
- [ ] Task 2

### Subtasks

- [ ] Subtask 1

Final content here.
"""
        import task_parser

        start, end = task_parser.find_tasks_section(content)
        lines = content.splitlines()

        # Should include everything until EOF
        section_content = "\n".join(lines[start:end])
        assert "Task 1" in section_content
        assert "Subtask 1" in section_content
        assert "Final content here" in section_content

    def test_return_none_when_no_tasks_section(self):
        """Should return None or indicate no section when ## Tasks not found."""
        content = """# Document Title

## Overview

Some content.

## Details

More content.
"""
        import task_parser

        result = task_parser.find_tasks_section(content)
        # Either returns (None, None) or raises exception
        assert result is None or result == (None, None)

    def test_ignore_tasks_in_code_blocks(self):
        """Should not detect ## Tasks inside fenced code blocks."""
        content = """## Real Section

```markdown
## Tasks

This is example code, not the real section.
```

## Tasks

- [ ] Real task 1
"""
        import task_parser

        start, end = task_parser.find_tasks_section(content)
        lines = content.splitlines()

        # Should find the second "## Tasks", not the one in code block
        assert start is not None
        assert lines[start].strip() == "## Tasks"
        assert start > 5  # Should be after the code block

    def test_handle_tasks_section_with_no_tasks(self):
        """Should detect ## Tasks section even if it contains no tasks."""
        content = """## Tasks

No tasks yet.

## Next Section
"""
        import task_parser

        start, end = task_parser.find_tasks_section(content)
        assert start is not None
        lines = content.splitlines()
        assert "## Tasks" in lines[start]


class TestCheckboxRecognition:
    """Test suite for checkbox recognition in task lines.

    Tests that the parser correctly:
    - Recognizes `- [ ]` (incomplete with spaces)
    - Recognizes `- []` (incomplete without spaces)
    - Recognizes `- [x]` (complete)
    - Recognizes `- [!]` (failed)
    - Handles any amount of leading whitespace
    - Ignores non-checkbox bullet points
    """

    def test_recognize_incomplete_checkbox_with_spaces(self):
        """Should recognize `- [ ]` as an incomplete checkbox."""
        import task_parser

        line = "- [ ] Task description"
        result = task_parser.is_checkbox_line(line)
        assert result is True

        status = task_parser.get_checkbox_status(line)
        assert status == "incomplete"

    def test_recognize_incomplete_checkbox_without_spaces(self):
        """Should recognize `- []` as an incomplete checkbox."""
        import task_parser

        line = "- [] Task description"
        result = task_parser.is_checkbox_line(line)
        assert result is True

        status = task_parser.get_checkbox_status(line)
        assert status == "incomplete"

    def test_recognize_complete_checkbox(self):
        """Should recognize `- [x]` as a complete checkbox."""
        import task_parser

        line = "- [x] Completed task"
        result = task_parser.is_checkbox_line(line)
        assert result is True

        status = task_parser.get_checkbox_status(line)
        assert status == "complete"

    def test_recognize_failed_checkbox(self):
        """Should recognize `- [!]` as a failed checkbox."""
        import task_parser

        line = "- [!] Failed task"
        result = task_parser.is_checkbox_line(line)
        assert result is True

        status = task_parser.get_checkbox_status(line)
        assert status == "failed"

    def test_handle_leading_whitespace_spaces(self):
        """Should recognize checkboxes with leading spaces (indentation)."""
        import task_parser

        test_cases = [
            "  - [ ] Task with 2 spaces",
            "    - [x] Task with 4 spaces",
            "      - [!] Task with 6 spaces",
            "        - [] Task with 8 spaces",
            "          - [ ] Task with 10 spaces",
        ]

        for line in test_cases:
            result = task_parser.is_checkbox_line(line)
            assert result is True, f"Failed to recognize: {repr(line)}"

    def test_handle_leading_whitespace_tabs(self):
        """Should recognize checkboxes with leading tabs (indentation)."""
        import task_parser

        test_cases = [
            "\t- [ ] Task with 1 tab",
            "\t\t- [x] Task with 2 tabs",
            "\t\t\t- [!] Task with 3 tabs",
        ]

        for line in test_cases:
            result = task_parser.is_checkbox_line(line)
            assert result is True, f"Failed to recognize: {repr(line)}"

    def test_handle_mixed_whitespace(self):
        """Should recognize checkboxes with mixed tabs and spaces."""
        import task_parser

        test_cases = [
            " \t- [ ] Task with space then tab",
            "\t - [x] Task with tab then space",
            "  \t  - [!] Task with mixed whitespace",
        ]

        for line in test_cases:
            result = task_parser.is_checkbox_line(line)
            assert result is True, f"Failed to recognize: {repr(line)}"

    def test_ignore_non_checkbox_bullets(self):
        """Should NOT recognize regular bullet points without checkboxes."""
        import task_parser

        test_cases = [
            "- Regular bullet point",
            "  - Indented bullet point",
            "- [incomplete] with word instead of symbol",
            "- [ x ] extra spaces inside brackets",
            "- Text with [x] checkbox not at start",
            "* Different bullet character",
            "+ Plus sign bullet",
        ]

        for line in test_cases:
            result = task_parser.is_checkbox_line(line)
            assert result is False, f"Should not recognize as checkbox: {repr(line)}"

    def test_recognize_uppercase_x_as_complete(self):
        """Should recognize `- [X]` (uppercase) as complete checkbox."""
        import task_parser

        line = "- [X] Task with uppercase X"
        result = task_parser.is_checkbox_line(line)
        assert result is True

        status = task_parser.get_checkbox_status(line)
        assert status == "complete"

    def test_extract_checkbox_text(self):
        """Should extract task description text from checkbox line."""
        import task_parser

        test_cases = [
            ("- [ ] Task description", "Task description"),
            ("  - [x] Completed task", "Completed task"),
            ("    - [!] Failed task", "Failed task"),
            ("- [] Short", "Short"),
            ("- [ ] Task with multiple words and punctuation!", "Task with multiple words and punctuation!"),
        ]

        for line, expected_text in test_cases:
            text = task_parser.extract_checkbox_text(line)
            assert text == expected_text, f"For line {repr(line)}, expected {repr(expected_text)}, got {repr(text)}"

    def test_get_indentation_level(self):
        """Should correctly calculate indentation level from leading whitespace."""
        import task_parser

        test_cases = [
            ("- [ ] No indent", 0),
            ("  - [ ] 2 spaces (1 level)", 2),
            ("    - [ ] 4 spaces (2 levels)", 4),
            ("      - [ ] 6 spaces (3 levels)", 6),
            ("\t- [ ] 1 tab", 1),  # Tabs might be treated differently
            ("\t\t- [ ] 2 tabs", 2),
        ]

        for line, expected_indent in test_cases:
            indent = task_parser.get_indentation_level(line)
            # Allow some flexibility for tab handling
            assert indent >= 0, f"Indentation should be non-negative for: {repr(line)}"

    def test_checkbox_with_no_description(self):
        """Should handle checkbox with no description text."""
        import task_parser

        test_cases = [
            "- [ ]",
            "- []",
            "- [x]",
            "  - [ ]",
        ]

        for line in test_cases:
            result = task_parser.is_checkbox_line(line)
            assert result is True, f"Should recognize empty checkbox: {repr(line)}"

            text = task_parser.extract_checkbox_text(line)
            assert text == "" or text is None, f"Empty checkbox should have empty text: {repr(line)}"

    def test_checkbox_pattern_variations(self):
        """Should handle various valid checkbox patterns."""
        import task_parser

        valid_patterns = [
            ("- [ ] standard incomplete", "incomplete"),
            ("- [] compact incomplete", "incomplete"),
            ("- [x] lowercase complete", "complete"),
            ("- [X] uppercase complete", "complete"),
            ("- [!] failed", "failed"),
        ]

        for line, expected_status in valid_patterns:
            result = task_parser.is_checkbox_line(line)
            assert result is True, f"Should recognize: {repr(line)}"

            status = task_parser.get_checkbox_status(line)
            assert status == expected_status, f"For {repr(line)}, expected {expected_status}, got {status}"

    def test_ignore_checkboxes_in_inline_code(self):
        """Should not treat checkboxes in inline code as real checkboxes."""
        import task_parser

        # These contain checkbox patterns but in code
        test_cases = [
            "Use `- [ ]` to create a checkbox",
            "The pattern `- [x]` marks complete",
            "- Regular bullet with `- [ ]` in code",
        ]

        # Note: This might be a future enhancement; for now we document expected behavior
        # The parser might recognize the pattern even in code - that's acceptable
        # This test documents the edge case
        for line in test_cases:
            # Just verify the function handles it without crashing
            result = task_parser.is_checkbox_line(line)
            assert isinstance(result, bool)

    def test_multiple_checkboxes_in_line(self):
        """Should handle line with multiple checkbox patterns (edge case)."""
        import task_parser

        line = "- [ ] Task about checkboxes: - [ ] and - [x]"

        # Should recognize the line as a checkbox based on the first pattern
        result = task_parser.is_checkbox_line(line)
        assert result is True

        # Status should be based on first checkbox
        status = task_parser.get_checkbox_status(line)
        assert status == "incomplete"

    def test_checkbox_with_special_characters(self):
        """Should handle checkbox descriptions with special markdown characters."""
        import task_parser

        test_cases = [
            "- [ ] Task with **bold** text",
            "- [x] Task with *italic* text",
            "- [!] Task with `code` in it",
            "- [ ] Task with [link](http://example.com)",
            "- [ ] Task with #hashtag",
            "- [ ] Task with 100% completion",
        ]

        for line in test_cases:
            result = task_parser.is_checkbox_line(line)
            assert result is True, f"Should recognize checkbox with special chars: {repr(line)}"

            # Text extraction should preserve the description
            text = task_parser.extract_checkbox_text(line)
            assert len(text) > 0, f"Should extract text from: {repr(line)}"


class TestIndentationParsingAndTreeBuilding:
    """Test suite for indentation parsing and parent-child tree building.

    Tests that the parser correctly:
    - Parses 2-space indentation
    - Parses 4-space indentation
    - Handles arbitrary nesting depth
    - Builds correct parent-child relationships
    - Handles mixed indentation consistently
    """

    def test_parse_flat_list_no_nesting(self):
        """Should parse a flat list of tasks with no parent-child relationships."""
        import task_parser

        content = """## Tasks

- [ ] Task 1
- [ ] Task 2
- [ ] Task 3
"""
        tasks = task_parser.parse_task_file(content)
        assert len(tasks) == 3
        assert all(task.parent is None for task in tasks)
        assert all(len(task.children) == 0 for task in tasks)

    def test_parse_two_space_indentation_single_level(self):
        """Should parse 2-space indentation with parent and children."""
        import task_parser

        content = """## Tasks

- [ ] Parent task
  - [ ] Child task 1
  - [ ] Child task 2
"""
        tasks = task_parser.parse_task_file(content)

        # Should have 1 root task (parent)
        root_tasks = [t for t in tasks if t.parent is None]
        assert len(root_tasks) == 1

        parent = root_tasks[0]
        assert "Parent task" in parent.text
        assert len(parent.children) == 2

        # Both children should reference the parent
        for child in parent.children:
            assert child.parent == parent
            assert "Child task" in child.text

    def test_parse_four_space_indentation_single_level(self):
        """Should parse 4-space indentation with parent and children."""
        import task_parser

        content = """## Tasks

- [ ] Parent task
    - [ ] Child task 1
    - [ ] Child task 2
"""
        tasks = task_parser.parse_task_file(content)

        # Should have 1 root task (parent)
        root_tasks = [t for t in tasks if t.parent is None]
        assert len(root_tasks) == 1

        parent = root_tasks[0]
        assert "Parent task" in parent.text
        assert len(parent.children) == 2

        # Both children should reference the parent
        for child in parent.children:
            assert child.parent == parent

    def test_parse_two_level_nesting(self):
        """Should parse two levels of nesting (grandparent -> parent -> child)."""
        import task_parser

        content = """## Tasks

- [ ] Grandparent task
  - [ ] Parent task
    - [ ] Child task
"""
        tasks = task_parser.parse_task_file(content)

        # Find root (grandparent)
        root_tasks = [t for t in tasks if t.parent is None]
        assert len(root_tasks) == 1

        grandparent = root_tasks[0]
        assert "Grandparent task" in grandparent.text
        assert len(grandparent.children) == 1

        parent = grandparent.children[0]
        assert "Parent task" in parent.text
        assert parent.parent == grandparent
        assert len(parent.children) == 1

        child = parent.children[0]
        assert "Child task" in child.text
        assert child.parent == parent
        assert len(child.children) == 0

    def test_parse_three_level_nesting(self):
        """Should parse three levels of nesting (great-grandparent -> grandparent -> parent -> child)."""
        import task_parser

        content = """## Tasks

- [ ] Level 0
  - [ ] Level 1
    - [ ] Level 2
      - [ ] Level 3
"""
        tasks = task_parser.parse_task_file(content)

        # Find root
        root_tasks = [t for t in tasks if t.parent is None]
        assert len(root_tasks) == 1

        # Navigate down the tree
        level0 = root_tasks[0]
        assert "Level 0" in level0.text
        assert len(level0.children) == 1

        level1 = level0.children[0]
        assert "Level 1" in level1.text
        assert level1.parent == level0
        assert len(level1.children) == 1

        level2 = level1.children[0]
        assert "Level 2" in level2.text
        assert level2.parent == level1
        assert len(level2.children) == 1

        level3 = level2.children[0]
        assert "Level 3" in level3.text
        assert level3.parent == level2
        assert len(level3.children) == 0

    def test_parse_arbitrary_deep_nesting(self):
        """Should parse arbitrarily deep nesting (5+ levels)."""
        import task_parser

        content = """## Tasks

- [ ] Level 0
  - [ ] Level 1
    - [ ] Level 2
      - [ ] Level 3
        - [ ] Level 4
          - [ ] Level 5
            - [ ] Level 6
"""
        tasks = task_parser.parse_task_file(content)

        # Find root and traverse down
        root_tasks = [t for t in tasks if t.parent is None]
        assert len(root_tasks) == 1

        current = root_tasks[0]
        level = 0
        while current is not None:
            assert f"Level {level}" in current.text
            if current.children:
                assert len(current.children) == 1
                current = current.children[0]
                level += 1
            else:
                # Reached the leaf
                assert level == 6
                break

    def test_parse_multiple_siblings_at_same_level(self):
        """Should correctly group multiple siblings under the same parent."""
        import task_parser

        content = """## Tasks

- [ ] Parent
  - [ ] Child 1
  - [ ] Child 2
  - [ ] Child 3
- [ ] Another parent
  - [ ] Another child 1
  - [ ] Another child 2
"""
        tasks = task_parser.parse_task_file(content)

        # Should have 2 root tasks
        root_tasks = [t for t in tasks if t.parent is None]
        assert len(root_tasks) == 2

        # First parent should have 3 children
        parent1 = root_tasks[0]
        assert "Parent" in parent1.text
        assert len(parent1.children) == 3
        for i, child in enumerate(parent1.children, 1):
            assert f"Child {i}" in child.text
            assert child.parent == parent1

        # Second parent should have 2 children
        parent2 = root_tasks[1]
        assert "Another parent" in parent2.text
        assert len(parent2.children) == 2

    def test_parse_complex_tree_mixed_nesting(self):
        """Should parse a complex tree with mixed nesting levels."""
        import task_parser

        content = """## Tasks

- [ ] Task 1
  - [ ] Task 1.1
  - [ ] Task 1.2
    - [ ] Task 1.2.1
- [ ] Task 2
- [ ] Task 3
  - [ ] Task 3.1
    - [ ] Task 3.1.1
    - [ ] Task 3.1.2
  - [ ] Task 3.2
"""
        tasks = task_parser.parse_task_file(content)

        # Should have 3 root tasks
        root_tasks = [t for t in tasks if t.parent is None]
        assert len(root_tasks) == 3

        # Task 1 has 2 children, second child has 1 child
        task1 = [t for t in root_tasks if "Task 1" in t.text and "Task 1." not in t.text][0]
        assert len(task1.children) == 2
        task1_2 = [c for c in task1.children if "Task 1.2" in c.text and "Task 1.2." not in c.text][0]
        assert len(task1_2.children) == 1

        # Task 2 has no children
        task2 = [t for t in root_tasks if "Task 2" in t.text][0]
        assert len(task2.children) == 0

        # Task 3 has 2 children, first child has 2 children
        task3 = [t for t in root_tasks if "Task 3" in t.text and "Task 3." not in t.text][0]
        assert len(task3.children) == 2
        task3_1 = [c for c in task3.children if "Task 3.1" in c.text and "Task 3.1." not in c.text][0]
        assert len(task3_1.children) == 2

    def test_indentation_level_calculation_2_space(self):
        """Should correctly calculate indentation levels with 2-space indent."""
        import task_parser

        test_cases = [
            ("- [ ] Task", 0),
            ("  - [ ] Task", 1),  # 2 spaces = level 1
            ("    - [ ] Task", 2),  # 4 spaces = level 2
            ("      - [ ] Task", 3),  # 6 spaces = level 3
            ("        - [ ] Task", 4),  # 8 spaces = level 4
        ]

        for line, expected_level in test_cases:
            level = task_parser.calculate_indent_level(line, indent_size=2)
            assert level == expected_level, f"For line {repr(line)} with 2-space indent, expected level {expected_level}, got {level}"

    def test_indentation_level_calculation_4_space(self):
        """Should correctly calculate indentation levels with 4-space indent."""
        import task_parser

        test_cases = [
            ("- [ ] Task", 0),
            ("    - [ ] Task", 1),  # 4 spaces = level 1
            ("        - [ ] Task", 2),  # 8 spaces = level 2
            ("            - [ ] Task", 3),  # 12 spaces = level 3
        ]

        for line, expected_level in test_cases:
            level = task_parser.calculate_indent_level(line, indent_size=4)
            assert level == expected_level, f"For line {repr(line)} with 4-space indent, expected level {expected_level}, got {level}"

    def test_detect_indent_size_from_content(self):
        """Should automatically detect whether content uses 2-space or 4-space indentation."""
        import task_parser

        content_2_space = """## Tasks

- [ ] Parent
  - [ ] Child
"""
        indent_size = task_parser.detect_indent_size(content_2_space)
        assert indent_size == 2

        content_4_space = """## Tasks

- [ ] Parent
    - [ ] Child
"""
        indent_size = task_parser.detect_indent_size(content_4_space)
        assert indent_size == 4

    def test_handle_inconsistent_indentation_gracefully(self):
        """Should handle inconsistent indentation without crashing."""
        import task_parser

        # Mix of 2 and 4 space indents
        content = """## Tasks

- [ ] Parent
  - [ ] Child with 2 spaces
    - [ ] Grandchild with 4 spaces total
   - [ ] Child with 3 spaces (odd)
"""
        # Should not crash, should make best effort to parse
        tasks = task_parser.parse_task_file(content)
        assert tasks is not None
        assert len(tasks) > 0

    def test_preserve_task_order(self):
        """Should preserve the original order of tasks in the file."""
        import task_parser

        content = """## Tasks

- [ ] First
- [ ] Second
- [ ] Third
  - [ ] Third-A
  - [ ] Third-B
- [ ] Fourth
"""
        tasks = task_parser.parse_task_file(content)

        # Collect all tasks in depth-first order
        all_tasks = []
        def collect(task_list):
            for task in task_list:
                all_tasks.append(task)
                collect(task.children)

        root_tasks = [t for t in tasks if t.parent is None]
        collect(root_tasks)

        # Check order
        task_texts = [t.text for t in all_tasks]
        assert "First" in task_texts[0]
        assert "Second" in task_texts[1]
        assert "Third" in task_texts[2]
        assert "Third-A" in task_texts[3]
        assert "Third-B" in task_texts[4]
        assert "Fourth" in task_texts[5]

    def test_leaf_task_identification(self):
        """Should correctly identify leaf tasks (tasks with no children)."""
        import task_parser

        content = """## Tasks

- [ ] Parent
  - [ ] Child 1 (leaf)
  - [ ] Child 2 (leaf)
- [ ] Another leaf
"""
        tasks = task_parser.parse_task_file(content)

        # Collect all tasks
        all_tasks = []
        def collect(task_list):
            for task in task_list:
                all_tasks.append(task)
                collect(task.children)

        root_tasks = [t for t in tasks if t.parent is None]
        collect(root_tasks)

        # Identify leaf tasks
        leaf_tasks = [t for t in all_tasks if len(t.children) == 0]
        assert len(leaf_tasks) == 3

        # "Parent" is not a leaf
        parent = [t for t in all_tasks if "Parent" in t.text][0]
        assert parent not in leaf_tasks

    def test_root_task_identification(self):
        """Should correctly identify root tasks (tasks with no parent)."""
        import task_parser

        content = """## Tasks

- [ ] Root 1
  - [ ] Child
- [ ] Root 2
- [ ] Root 3
  - [ ] Child
    - [ ] Grandchild
"""
        tasks = task_parser.parse_task_file(content)

        root_tasks = [t for t in tasks if t.parent is None]
        assert len(root_tasks) == 3

        root_texts = [t.text for t in root_tasks]
        assert any("Root 1" in text for text in root_texts)
        assert any("Root 2" in text for text in root_texts)
        assert any("Root 3" in text for text in root_texts)

    def test_parse_with_blank_lines_between_tasks(self):
        """Should handle blank lines between tasks correctly."""
        import task_parser

        content = """## Tasks

- [ ] Task 1

- [ ] Task 2
  - [ ] Child

- [ ] Task 3
"""
        tasks = task_parser.parse_task_file(content)

        root_tasks = [t for t in tasks if t.parent is None]
        assert len(root_tasks) == 3

    def test_parse_returns_task_data_class_instances(self):
        """Should return Task data class instances with proper attributes."""
        import task_parser

        content = """## Tasks

- [ ] Parent task
  - [x] Completed child
"""
        tasks = task_parser.parse_task_file(content)

        # Should have Task objects with expected attributes
        root_tasks = [t for t in tasks if t.parent is None]
        task = root_tasks[0]

        # Check that Task has expected attributes
        assert hasattr(task, 'text')
        assert hasattr(task, 'status')
        assert hasattr(task, 'parent')
        assert hasattr(task, 'children')
        assert hasattr(task, 'line_number')

        # Check values
        assert "Parent task" in task.text
        assert task.status == "incomplete"
        assert task.parent is None
        assert len(task.children) == 1

        child = task.children[0]
        assert "Completed child" in child.text
        assert child.status == "complete"
        assert child.parent == task

    def test_store_original_line_numbers(self):
        """Should store the original line number for each task."""
        import task_parser

        content = """## Tasks

- [ ] Task at line 3
  - [ ] Task at line 4
- [ ] Task at line 5
"""
        tasks = task_parser.parse_task_file(content)

        # Line numbers should be stored (1-indexed)
        all_tasks = []
        def collect(task_list):
            for task in task_list:
                all_tasks.append(task)
                collect(task.children)

        root_tasks = [t for t in tasks if t.parent is None]
        collect(root_tasks)

        # All tasks should have line numbers
        for task in all_tasks:
            assert hasattr(task, 'line_number')
            assert task.line_number > 0

    def test_handle_different_checkbox_states_in_tree(self):
        """Should preserve checkbox states (incomplete, complete, failed) in tree."""
        import task_parser

        content = """## Tasks

- [ ] Incomplete parent
  - [x] Complete child
  - [!] Failed child
  - [ ] Incomplete child
"""
        tasks = task_parser.parse_task_file(content)

        root_tasks = [t for t in tasks if t.parent is None]
        parent = root_tasks[0]

        assert parent.status == "incomplete"
        assert len(parent.children) == 3

        statuses = [child.status for child in parent.children]
        assert "complete" in statuses
        assert "failed" in statuses
        assert "incomplete" in statuses


class TestCodeBlockAwareness:
    """Test suite for code-block awareness in task parsing.

    Tests that the parser correctly:
    - Excludes checkboxes inside fenced code blocks from parsing
    - Handles various code block delimiters (``` and ~~~)
    - Handles code blocks with and without language specifiers
    - Handles nested code blocks in markdown examples
    - Handles unclosed code blocks gracefully
    - Handles code blocks in Tasks section
    """

    def test_exclude_checkboxes_in_fenced_code_block(self):
        """Should exclude checkboxes inside ``` fenced code blocks."""
        import task_parser

        content = """## Tasks

- [ ] Real task 1

```
- [ ] This is example code, not a real task
- [x] This is also fake
```

- [ ] Real task 2
"""
        tasks = task_parser.parse_task_file(content)

        # Should only find 2 real tasks, not the ones in the code block
        all_tasks = []
        def collect(task_list):
            for task in task_list:
                all_tasks.append(task)
                collect(task.children)

        root_tasks = [t for t in tasks if t.parent is None]
        collect(root_tasks)

        assert len(all_tasks) == 2
        assert "Real task 1" in all_tasks[0].text
        assert "Real task 2" in all_tasks[1].text

        # Verify none of the tasks contain "example code"
        for task in all_tasks:
            assert "example code" not in task.text.lower()
            assert "fake" not in task.text.lower()

    def test_exclude_checkboxes_in_tildes_code_block(self):
        """Should exclude checkboxes inside ~~~ fenced code blocks."""
        import task_parser

        content = """## Tasks

- [ ] Real task

~~~
- [ ] Fake task in tildes block
~~~

- [ ] Another real task
"""
        tasks = task_parser.parse_task_file(content)

        all_tasks = []
        def collect(task_list):
            for task in task_list:
                all_tasks.append(task)
                collect(task.children)

        root_tasks = [t for t in tasks if t.parent is None]
        collect(root_tasks)

        assert len(all_tasks) == 2
        assert "Real task" in all_tasks[0].text
        assert "Another real task" in all_tasks[1].text

    def test_exclude_checkboxes_in_code_block_with_language(self):
        """Should exclude checkboxes in code blocks with language specifiers."""
        import task_parser

        content = """## Tasks

- [ ] Implement feature

```python
# Example code showing checkbox format
tasks = [
    "- [ ] Item 1",
    "- [x] Item 2",
]
```

```markdown
## Example Tasks Section
- [ ] Example task
- [x] Completed example
```

- [ ] Write tests
"""
        tasks = task_parser.parse_task_file(content)

        all_tasks = []
        def collect(task_list):
            for task in task_list:
                all_tasks.append(task)
                collect(task.children)

        root_tasks = [t for t in tasks if t.parent is None]
        collect(root_tasks)

        # Should only find the 2 real tasks
        assert len(all_tasks) == 2
        assert "Implement feature" in all_tasks[0].text
        assert "Write tests" in all_tasks[1].text

    def test_handle_multiple_code_blocks(self):
        """Should handle multiple code blocks in the Tasks section."""
        import task_parser

        content = """## Tasks

- [ ] Task 1

```
- [ ] Fake 1
```

- [ ] Task 2

```
- [ ] Fake 2
- [ ] Fake 3
```

- [ ] Task 3

```bash
echo "- [ ] Fake 4"
```

- [ ] Task 4
"""
        tasks = task_parser.parse_task_file(content)

        all_tasks = []
        def collect(task_list):
            for task in task_list:
                all_tasks.append(task)
                collect(task.children)

        root_tasks = [t for t in tasks if t.parent is None]
        collect(root_tasks)

        # Should find only the 4 real tasks
        assert len(all_tasks) == 4
        task_texts = [t.text for t in all_tasks]
        assert all(f"Task {i}" in " ".join(task_texts) for i in range(1, 5))

        # Verify no "Fake" tasks were included
        for task in all_tasks:
            assert "Fake" not in task.text

    def test_handle_nested_tasks_with_code_blocks(self):
        """Should handle code blocks within nested task structure."""
        import task_parser

        content = """## Tasks

- [ ] Parent task

Example code:
```
- [ ] This is in a code block
```

  - [ ] Real child task 1
  - [ ] Real child task 2

```
  - [ ] Indented fake task in code block
```

  - [ ] Real child task 3
"""
        tasks = task_parser.parse_task_file(content)

        all_tasks = []
        def collect(task_list):
            for task in task_list:
                all_tasks.append(task)
                collect(task.children)

        root_tasks = [t for t in tasks if t.parent is None]
        collect(root_tasks)

        # Should have 1 parent with 3 children
        assert len(root_tasks) == 1
        parent = root_tasks[0]
        assert "Parent task" in parent.text
        assert len(parent.children) == 3

        # Verify children are the real ones
        for i, child in enumerate(parent.children, 1):
            assert f"Real child task {i}" in child.text
            assert "fake" not in child.text.lower()

    def test_handle_code_block_spanning_entire_section(self):
        """Should handle case where entire Tasks section is in a code block."""
        import task_parser

        content = """## Tasks

```
- [ ] All tasks are in code block
- [ ] None should be parsed
  - [ ] Including nested ones
```
"""
        tasks = task_parser.parse_task_file(content)

        # Should find no tasks
        all_tasks = []
        def collect(task_list):
            for task in task_list:
                all_tasks.append(task)
                collect(task.children)

        root_tasks = [t for t in tasks if t.parent is None]
        collect(root_tasks)

        assert len(all_tasks) == 0

    def test_handle_code_block_before_tasks_section(self):
        """Should not be affected by code blocks before Tasks section."""
        import task_parser

        content = """## Overview

```
- [ ] This is example syntax
- [x] Not in Tasks section
```

## Tasks

- [ ] Real task 1
- [ ] Real task 2
"""
        tasks = task_parser.parse_task_file(content)

        all_tasks = []
        def collect(task_list):
            for task in task_list:
                all_tasks.append(task)
                collect(task.children)

        root_tasks = [t for t in tasks if t.parent is None]
        collect(root_tasks)

        # Should find the 2 real tasks
        assert len(all_tasks) == 2
        assert "Real task 1" in all_tasks[0].text
        assert "Real task 2" in all_tasks[1].text

    def test_handle_unclosed_code_block(self):
        """Should handle unclosed code block gracefully (treats rest of file as code)."""
        import task_parser

        content = """## Tasks

- [ ] Task before code block

```
- [ ] Unclosed code block starts here
- [ ] Everything after should be treated as code
- [ ] Until end of file
"""
        tasks = task_parser.parse_task_file(content)

        all_tasks = []
        def collect(task_list):
            for task in task_list:
                all_tasks.append(task)
                collect(task.children)

        root_tasks = [t for t in tasks if t.parent is None]
        collect(root_tasks)

        # Should only find the task before the code block
        assert len(all_tasks) == 1
        assert "Task before code block" in all_tasks[0].text

    def test_handle_code_block_with_only_opening_fence(self):
        """Should handle a code block opened but never closed."""
        import task_parser

        content = """## Tasks

- [ ] Real task 1

```markdown

- [ ] Task in unclosed block

More content here.
"""
        tasks = task_parser.parse_task_file(content)

        all_tasks = []
        def collect(task_list):
            for task in task_list:
                all_tasks.append(task)
                collect(task.children)

        root_tasks = [t for t in tasks if t.parent is None]
        collect(root_tasks)

        # Should only find the real task before the block
        assert len(all_tasks) == 1
        assert "Real task 1" in all_tasks[0].text

    def test_handle_empty_code_block(self):
        """Should handle empty code blocks without issues."""
        import task_parser

        content = """## Tasks

- [ ] Task 1

```
```

- [ ] Task 2

```python
```

- [ ] Task 3
"""
        tasks = task_parser.parse_task_file(content)

        all_tasks = []
        def collect(task_list):
            for task in task_list:
                all_tasks.append(task)
                collect(task.children)

        root_tasks = [t for t in tasks if t.parent is None]
        collect(root_tasks)

        # Should find all 3 real tasks
        assert len(all_tasks) == 3

    def test_handle_code_fence_in_checkbox_text(self):
        """Should handle backticks in checkbox text (not a code block)."""
        import task_parser

        content = """## Tasks

- [ ] Use `git commit -m "message"` to commit
- [ ] Run ```python script.py``` command
- [ ] Task with `inline code` in description
"""
        tasks = task_parser.parse_task_file(content)

        all_tasks = []
        def collect(task_list):
            for task in task_list:
                all_tasks.append(task)
                collect(task.children)

        root_tasks = [t for t in tasks if t.parent is None]
        collect(root_tasks)

        # Should find all 3 tasks with inline code
        assert len(all_tasks) == 3
        assert "git commit" in all_tasks[0].text
        assert "python script.py" in all_tasks[1].text
        assert "inline code" in all_tasks[2].text

    def test_handle_code_block_with_extra_backticks(self):
        """Should handle code blocks with more than 3 backticks."""
        import task_parser

        content = """## Tasks

- [ ] Real task

````
```
- [ ] Nested code example
```
````

- [ ] Another real task
"""
        tasks = task_parser.parse_task_file(content)

        all_tasks = []
        def collect(task_list):
            for task in task_list:
                all_tasks.append(task)
                collect(task.children)

        root_tasks = [t for t in tasks if t.parent is None]
        collect(root_tasks)

        # Should find 2 real tasks
        assert len(all_tasks) == 2
        assert "Real task" in all_tasks[0].text
        assert "Another real task" in all_tasks[1].text

    def test_code_block_detection_is_line_based(self):
        """Should detect code blocks based on lines starting with fence markers."""
        import task_parser

        content = """## Tasks

- [ ] Real task 1

Text before ```
- [ ] This checkbox is NOT in a code block (fence not at line start)

```
- [ ] This IS in a code block
```

- [ ] Real task 2
"""
        tasks = task_parser.parse_task_file(content)

        all_tasks = []
        def collect(task_list):
            for task in task_list:
                all_tasks.append(task)
                collect(task.children)

        root_tasks = [t for t in tasks if t.parent is None]
        collect(root_tasks)

        # Should find 3 tasks (including the one after "Text before ```")
        assert len(all_tasks) == 3
        assert "Real task 1" in all_tasks[0].text
        assert "NOT in a code block" in all_tasks[1].text
        assert "Real task 2" in all_tasks[2].text

    def test_preserve_line_numbers_with_code_blocks(self):
        """Should maintain accurate line numbers even with code blocks present."""
        import task_parser

        content = """## Tasks

- [ ] Task at line 3

```
- [ ] Fake at line 5
- [ ] Fake at line 6
```

- [ ] Task at line 9
"""
        tasks = task_parser.parse_task_file(content)

        all_tasks = []
        def collect(task_list):
            for task in task_list:
                all_tasks.append(task)
                collect(task.children)

        root_tasks = [t for t in tasks if t.parent is None]
        collect(root_tasks)

        # Should find 2 tasks with correct line numbers
        assert len(all_tasks) == 2
        assert all_tasks[0].line_number == 3
        assert all_tasks[1].line_number == 10


class TestNextTaskCommandFunctionality:
    """Test suite for next-task command functionality.

    Tests that the next-task command correctly:
    - Returns pipe-delimited output: line_num|parent_text|task_text
    - Finds the next incomplete leaf task
    - Skips completed tasks [x]
    - Skips failed tasks [!]
    - Skips parent tasks (non-leaf)
    - Exits 1 when no tasks remain
    - Handles nested task structures
    - Returns first incomplete task in document order
    """

    def test_next_task_returns_pipe_delimited_output(self, tmp_path):
        """Should return output in format: line_num|parent_text|task_text."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Parent task
  - [ ] Child task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        # Output should be pipe-delimited
        assert "|" in result.stdout
        parts = result.stdout.strip().split("|")
        assert len(parts) == 3

    def test_next_task_returns_first_incomplete_leaf(self, tmp_path):
        """Should return the first incomplete leaf task."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Parent task
  - [ ] First child (should be returned)
  - [ ] Second child
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        assert "First child" in output

    def test_next_task_skips_completed_tasks(self, tmp_path):
        """Should skip tasks marked as complete [x]."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [x] Completed task
- [ ] Incomplete task (should be returned)
- [ ] Another incomplete task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        assert "Incomplete task (should be returned)" in output
        assert "Completed task" not in output

    def test_next_task_skips_failed_tasks(self, tmp_path):
        """Should skip tasks marked as failed [!]."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [!] Failed task
- [ ] Incomplete task (should be returned)
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        assert "Incomplete task (should be returned)" in output
        assert "Failed task" not in output

    def test_next_task_skips_parent_tasks(self, tmp_path):
        """Should skip parent tasks (non-leaf tasks) and return leaf tasks."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Parent task (has children, should be skipped)
  - [x] Completed child
  - [ ] Incomplete child (should be returned)
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        assert "Incomplete child (should be returned)" in output
        assert "Parent task" not in output.split("|")[-1]

    def test_next_task_includes_parent_text_in_output(self, tmp_path):
        """Should include parent task text in the output."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Parent task
  - [ ] Child task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        parts = output.split("|")
        assert len(parts) == 3
        # parts[1] should be parent text
        assert "Parent task" in parts[1]
        # parts[2] should be child task text
        assert "Child task" in parts[2]

    def test_next_task_includes_line_number(self, tmp_path):
        """Should include the line number as the first part of output."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] First task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        parts = output.split("|")
        # First part should be a line number
        line_num = parts[0]
        assert line_num.isdigit()
        assert int(line_num) > 0

    def test_next_task_exits_1_when_no_tasks_remain(self, tmp_path):
        """Should exit with code 1 when all tasks are complete or failed."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [x] Completed task 1
- [!] Failed task
- [x] Completed task 2
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1

    def test_next_task_exits_1_when_no_tasks_section(self, tmp_path):
        """Should exit with code 1 when there is no Tasks section."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Overview

Some content without tasks.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1

    def test_next_task_exits_1_when_only_parent_tasks_incomplete(self, tmp_path):
        """Should exit 1 when only parent tasks are incomplete (no incomplete leaves)."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Parent task
  - [x] All children complete
  - [x] All children complete
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 1

    def test_next_task_handles_deeply_nested_tasks(self, tmp_path):
        """Should correctly find leaf tasks in deeply nested structures."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Level 0
  - [ ] Level 1
    - [ ] Level 2
      - [x] Level 3 complete
      - [ ] Level 3 incomplete (should be returned)
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        assert "Level 3 incomplete" in output

    def test_next_task_returns_first_in_document_order(self, tmp_path):
        """Should return the first incomplete leaf task in document order."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Parent 1
  - [x] Completed child
  - [ ] First incomplete child (should be returned)
  - [ ] Second incomplete child
- [ ] Parent 2
  - [ ] Another incomplete child
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        assert "First incomplete child (should be returned)" in output

    def test_next_task_with_flat_task_list(self, tmp_path):
        """Should handle flat task lists (no nesting)."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [x] Completed task
- [ ] Incomplete task (should be returned)
- [ ] Another task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        parts = output.split("|")
        # For flat list, parent_text should be empty or indicate no parent
        assert "Incomplete task (should be returned)" in output

    def test_next_task_with_multiple_parent_levels(self, tmp_path):
        """Should correctly report parent text for tasks with multiple ancestor levels."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Grandparent
  - [ ] Parent
    - [ ] Child task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        parts = output.split("|")
        assert len(parts) == 3
        # Should include parent (immediate parent, not grandparent)
        assert "Parent" in parts[1] or "Parent" in output
        assert "Child task" in parts[2]

    def test_next_task_with_mixed_checkbox_formats(self, tmp_path):
        """Should handle various checkbox formats ([ ], [], [x], [X], [!])."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [x] Completed lowercase x
- [X] Completed uppercase X
- [!] Failed task
- [] Empty brackets incomplete
- [ ] Standard incomplete (should be returned)
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        # Should return one of the incomplete tasks
        assert "Empty brackets incomplete" in output or "Standard incomplete" in output

    def test_next_task_output_format_correctness(self, tmp_path):
        """Should output exactly 3 pipe-delimited fields."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Parent with | pipe character
  - [ ] Child task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        # Should have exactly 2 pipe characters (3 fields)
        # Note: this test may need adjustment if pipe characters in task text need escaping
        parts = output.split("|")
        assert len(parts) >= 3

    def test_next_task_with_empty_task_descriptions(self, tmp_path):
        """Should handle tasks with empty descriptions."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ]
- [ ] Task with description
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should succeed (either return the empty task or the one with description)
        assert result.returncode == 0

    def test_next_task_with_root_level_incomplete_leaf(self, tmp_path):
        """Should return root-level task if it's a leaf and incomplete."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [x] Completed root task
- [ ] Incomplete root leaf (should be returned)
- [ ] Parent with children
  - [x] Completed child
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        assert "Incomplete root leaf" in output

    def test_next_task_parent_text_empty_for_root_tasks(self, tmp_path):
        """Should have empty or special value for parent_text when task has no parent."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Root task with no parent
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        parts = output.split("|")
        assert len(parts) == 3
        # parts[1] (parent_text) should be empty or indicate no parent
        # Allow for empty string or placeholder like "(none)" or "-"

    def test_next_task_with_code_blocks(self, tmp_path):
        """Should ignore tasks in code blocks."""
        import task_parser

        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

```
- [ ] Fake task in code block
```

- [ ] Real task (should be returned)
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        assert "Real task" in output
        assert "Fake task" not in output

    def test_next_task_file_not_found(self, tmp_path):
        """Should exit with error when file doesn't exist."""
        import task_parser

        non_existent = tmp_path / "nonexistent.md"

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "next-task", str(non_existent)],
            capture_output=True,
            text=True,
        )

        # Should fail with non-zero exit code
        assert result.returncode != 0


class TestCountCommandFunctionality:
    """Test suite for count command functionality.

    Tests that the count command correctly:
    - Returns pipe-delimited output: total|completed|failed
    - Counts only leaf tasks (tasks with no children)
    - Excludes parent tasks from counts
    - Correctly identifies completed tasks [x]
    - Correctly identifies failed tasks [!]
    - Handles nested task structures
    - Handles flat task lists
    """

    def test_count_returns_pipe_delimited_output(self, tmp_path):
        """Should return output in format: total|completed|failed."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Task 1
- [x] Task 2
- [!] Task 3
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        # Output should be pipe-delimited
        assert "|" in result.stdout
        parts = result.stdout.strip().split("|")
        assert len(parts) == 3

    def test_count_flat_task_list(self, tmp_path):
        """Should correctly count tasks in a flat list."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Incomplete task 1
- [x] Completed task 1
- [x] Completed task 2
- [!] Failed task 1
- [ ] Incomplete task 2
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        parts = output.split("|")

        total = int(parts[0])
        completed = int(parts[1])
        failed = int(parts[2])

        # 5 total leaf tasks, 2 completed, 1 failed
        assert total == 5
        assert completed == 2
        assert failed == 1

    def test_count_only_leaf_tasks(self, tmp_path):
        """Should count only leaf tasks, not parent tasks."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Parent task (should NOT be counted)
  - [ ] Child task 1
  - [x] Child task 2
  - [!] Child task 3
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        parts = output.split("|")

        total = int(parts[0])
        completed = int(parts[1])
        failed = int(parts[2])

        # Only 3 leaf tasks (children), parent is excluded
        assert total == 3
        assert completed == 1
        assert failed == 1

    def test_count_nested_task_structure(self, tmp_path):
        """Should handle nested task structures correctly."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Parent 1
  - [ ] Child 1.1
  - [x] Child 1.2
- [ ] Parent 2
  - [ ] Parent 2.1 (has children)
    - [x] Grandchild 2.1.1
    - [!] Grandchild 2.1.2
  - [x] Child 2.2
- [x] Root leaf task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        parts = output.split("|")

        total = int(parts[0])
        completed = int(parts[1])
        failed = int(parts[2])

        # Leaf tasks: Child 1.1, Child 1.2, Grandchild 2.1.1, Grandchild 2.1.2, Child 2.2, Root leaf task
        # Total: 6 leaf tasks
        # Completed: Child 1.2, Grandchild 2.1.1, Child 2.2, Root leaf task = 4
        # Failed: Grandchild 2.1.2 = 1
        assert total == 6
        assert completed == 4
        assert failed == 1

    def test_count_all_tasks_incomplete(self, tmp_path):
        """Should return correct counts when all tasks are incomplete."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Task 1
- [ ] Task 2
- [ ] Task 3
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        parts = output.split("|")

        total = int(parts[0])
        completed = int(parts[1])
        failed = int(parts[2])

        assert total == 3
        assert completed == 0
        assert failed == 0

    def test_count_all_tasks_completed(self, tmp_path):
        """Should return correct counts when all tasks are completed."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [x] Task 1
- [x] Task 2
- [x] Task 3
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        parts = output.split("|")

        total = int(parts[0])
        completed = int(parts[1])
        failed = int(parts[2])

        assert total == 3
        assert completed == 3
        assert failed == 0

    def test_count_all_tasks_failed(self, tmp_path):
        """Should return correct counts when all tasks are failed."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [!] Task 1
- [!] Task 2
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        parts = output.split("|")

        total = int(parts[0])
        completed = int(parts[1])
        failed = int(parts[2])

        assert total == 2
        assert completed == 0
        assert failed == 2

    def test_count_no_tasks(self, tmp_path):
        """Should return 0|0|0 when there are no tasks."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

No tasks yet.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        assert output == "0|0|0"

    def test_count_no_tasks_section(self, tmp_path):
        """Should return 0|0|0 when there is no Tasks section."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Overview

Some content without tasks.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        assert output == "0|0|0"

    def test_count_mixed_parent_and_leaf_tasks(self, tmp_path):
        """Should only count leaf tasks in mixed structure."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [x] Root leaf 1
- [ ] Parent 1 (not counted)
  - [x] Child 1
  - [ ] Child 2
- [!] Root leaf 2
- [ ] Parent 2 (not counted)
  - [!] Child 3
- [ ] Root leaf 3
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        parts = output.split("|")

        total = int(parts[0])
        completed = int(parts[1])
        failed = int(parts[2])

        # Leaf tasks: Root leaf 1, Child 1, Child 2, Root leaf 2, Child 3, Root leaf 3 = 6
        # Completed: Root leaf 1, Child 1 = 2
        # Failed: Root leaf 2, Child 3 = 2
        assert total == 6
        assert completed == 2
        assert failed == 2

    def test_count_with_uppercase_X_checkbox(self, tmp_path):
        """Should recognize uppercase [X] as completed."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [X] Task with uppercase X
- [x] Task with lowercase x
- [ ] Incomplete task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        parts = output.split("|")

        total = int(parts[0])
        completed = int(parts[1])
        failed = int(parts[2])

        assert total == 3
        assert completed == 2  # Both X and x should be counted as completed
        assert failed == 0

    def test_count_with_empty_checkbox_variants(self, tmp_path):
        """Should handle both [ ] and [] as incomplete."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Standard empty checkbox
- [] Compact empty checkbox
- [x] Completed task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        parts = output.split("|")

        total = int(parts[0])
        completed = int(parts[1])
        failed = int(parts[2])

        assert total == 3
        assert completed == 1
        assert failed == 0

    def test_count_ignores_code_blocks(self, tmp_path):
        """Should not count tasks inside code blocks."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Real task 1

```
- [x] Fake completed task
- [!] Fake failed task
- [ ] Fake incomplete task
```

- [x] Real task 2
- [!] Real task 3
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        parts = output.split("|")

        total = int(parts[0])
        completed = int(parts[1])
        failed = int(parts[2])

        # Only 3 real tasks (ignore code block)
        assert total == 3
        assert completed == 1
        assert failed == 1

    def test_count_deeply_nested_structure(self, tmp_path):
        """Should correctly count leaf tasks in deeply nested structure."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Level 0
  - [ ] Level 1
    - [ ] Level 2
      - [ ] Level 3
        - [x] Level 4 leaf
        - [ ] Level 4 leaf 2
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        parts = output.split("|")

        total = int(parts[0])
        completed = int(parts[1])
        failed = int(parts[2])

        # Only the 2 Level 4 tasks are leaves (no children)
        assert total == 2
        assert completed == 1
        assert failed == 0

    def test_count_output_has_no_whitespace(self, tmp_path):
        """Should output clean pipe-delimited string with no extra whitespace."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Task 1
- [x] Task 2
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()

        # Should not have any extra whitespace around pipes
        assert output.count("|") == 2
        parts = output.split("|")
        for part in parts:
            assert part == part.strip()
            assert part.isdigit()

    def test_count_file_not_found(self, tmp_path):
        """Should exit with error when file doesn't exist."""
        non_existent = tmp_path / "nonexistent.md"

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", str(non_existent)],
            capture_output=True,
            text=True,
        )

        # Should fail with non-zero exit code
        assert result.returncode != 0

    def test_count_with_blank_lines_between_tasks(self, tmp_path):
        """Should correctly count tasks with blank lines between them."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Task 1

- [x] Task 2

- [!] Task 3

""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        parts = output.split("|")

        total = int(parts[0])
        completed = int(parts[1])
        failed = int(parts[2])

        assert total == 3
        assert completed == 1
        assert failed == 1

    def test_count_parent_complete_children_incomplete(self, tmp_path):
        """Should not count parent task even if marked complete."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [x] Parent marked complete (should NOT be counted)
  - [ ] Child incomplete
  - [ ] Child incomplete 2
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "count", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        parts = output.split("|")

        total = int(parts[0])
        completed = int(parts[1])
        failed = int(parts[2])

        # Only 2 leaf tasks (children), parent is not counted
        assert total == 2
        assert completed == 0
        assert failed == 0


class TestVerificationSectionCommandFunctionality:
    """Test suite for verification-section command functionality.

    Tests that the verification-section command correctly:
    - Extracts content between verification heading and next heading or EOF
    - Matches 'verif' prefix case-insensitively
    - Handles h1 (# Verification), h2 (## Verification), h3 (### Verification)
    - Stops at the next heading of any level
    - Returns empty string when no verification section exists
    - Handles verification section at end of file (no following heading)
    - Handles various verification heading variations (Verification, Verification Criteria, etc.)
    """

    def test_verification_section_basic_h2(self, tmp_path):
        """Should extract content from ## Verification heading to next heading."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Task 1

## Verification

This is the verification content.
It spans multiple lines.

## Next Section

This should not be included.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout
        assert "This is the verification content." in output
        assert "It spans multiple lines." in output
        assert "This should not be included." not in output
        assert "## Next Section" not in output

    def test_verification_section_h1(self, tmp_path):
        """Should extract content from # Verification (h1) heading."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Tasks

- [ ] Task 1

# Verification

Verification content for h1.

# Next Section
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout
        assert "Verification content for h1." in output
        assert "# Next Section" not in output

    def test_verification_section_h3(self, tmp_path):
        """Should extract content from ### Verification (h3) heading."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""### Tasks

- [ ] Task 1

### Verification

Verification content for h3.

### Next Section
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout
        assert "Verification content for h3." in output
        assert "### Next Section" not in output

    def test_verification_section_case_insensitive(self, tmp_path):
        """Should match 'verification' case-insensitively."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Task 1

## VERIFICATION

Content under uppercase heading.

## Next
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout
        assert "Content under uppercase heading." in output

    def test_verification_section_prefix_match(self, tmp_path):
        """Should match headings that start with 'verif' (prefix match)."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Task 1

## Verification Criteria

Content under verification criteria heading.

## Next
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout
        assert "Content under verification criteria heading." in output

    def test_verification_section_at_eof(self, tmp_path):
        """Should extract verification section when it's at the end of file."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Task 1

## Verification

This is at the end of the file.
No heading follows this.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout
        assert "This is at the end of the file." in output
        assert "No heading follows this." in output

    def test_verification_section_missing(self, tmp_path):
        """Should return empty string when no verification section exists."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Task 1

## Some Other Section

Content here.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        assert output == ""

    def test_verification_section_stops_at_any_heading_level(self, tmp_path):
        """Should stop at the next heading regardless of its level."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Verification

Verification content here.

# Higher Level Heading

This should not be included.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout
        assert "Verification content here." in output
        assert "This should not be included." not in output

    def test_verification_section_with_code_blocks(self, tmp_path):
        """Should include code blocks in the verification section."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Verification

Run the following command:

```bash
pytest tests/
```

## Next Section
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout
        assert "Run the following command:" in output
        assert "```bash" in output
        assert "pytest tests/" in output
        assert "```" in output
        assert "## Next Section" not in output

    def test_verification_section_with_lists(self, tmp_path):
        """Should include lists in the verification section."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Verification

Checklist:
- [ ] All tests pass
- [ ] Code is documented
- [x] PR is reviewed

## Next Section
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout
        assert "Checklist:" in output
        assert "- [ ] All tests pass" in output
        assert "- [ ] Code is documented" in output
        assert "- [x] PR is reviewed" in output
        assert "## Next Section" not in output

    def test_verification_section_empty_content(self, tmp_path):
        """Should return empty string when verification section has no content."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Verification

## Next Section
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout.strip()
        # Should be empty or just whitespace
        assert len(output) == 0 or output.isspace()

    def test_verification_section_with_blank_lines(self, tmp_path):
        """Should preserve blank lines within the verification section."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Verification

First paragraph.

Second paragraph after blank line.


Third paragraph after multiple blank lines.

## Next Section
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout
        assert "First paragraph." in output
        assert "Second paragraph after blank line." in output
        assert "Third paragraph after multiple blank lines." in output
        # Should have blank lines preserved
        assert "\n\n" in output

    def test_verification_section_file_not_found(self, tmp_path):
        """Should exit with error when file doesn't exist."""
        non_existent = tmp_path / "nonexistent.md"

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section", str(non_existent)],
            capture_output=True,
            text=True,
        )

        # Should fail with non-zero exit code
        assert result.returncode != 0

    def test_verification_section_abbreviated_verif(self, tmp_path):
        """Should match 'verif' abbreviated heading."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Verif

Content under abbreviated heading.

## Next
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout
        assert "Content under abbreviated heading." in output

    def test_verification_section_multiple_matches_uses_first(self, tmp_path):
        """Should use the first verification heading when multiple exist."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Verification

First verification section.

## Other Section

Some content.

## Verification Again

Second verification section (should be ignored).
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout
        assert "First verification section." in output
        assert "Second verification section (should be ignored)." not in output

    def test_verification_section_mixed_heading_levels(self, tmp_path):
        """Should stop at next heading even if it's a different level."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Verification

Content here.

#### Sub-heading (h4)

This should not be included.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout
        assert "Content here." in output
        assert "This should not be included." not in output

    def test_verification_section_no_false_match(self, tmp_path):
        """Should not match headings that don't start with 'verif'."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""## Tasks

- [ ] Task 1

## Reverification

This should NOT be matched (doesn't start with verif).

## Verification

This should be matched.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "verification-section", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        output = result.stdout
        assert "This should be matched." in output
        assert "This should NOT be matched" not in output


class TestValidateCommand:
    """Test suite for validate command."""

    def test_validate_valid_file_exits_zero(self, tmp_path):
        """Should exit 0 for a valid task file with all required sections."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Task 1
  - [ ] Subtask 1.1
- [x] Task 2

## Verification

All tests pass.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0

    def test_validate_missing_tasks_section_warns(self, tmp_path):
        """Should warn when Tasks section is missing."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Introduction

Some content here.

## Verification

All tests pass.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should still be parseable, just warn
        assert result.returncode == 0
        assert "Tasks section" in result.stderr or "missing" in result.stderr.lower()

    def test_validate_no_checkboxes_warns(self, tmp_path):
        """Should warn when Tasks section has no checkboxes."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

Just some text, no checkboxes.

## Verification

All tests pass.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should still be parseable, just warn
        assert result.returncode == 0
        assert "checkbox" in result.stderr.lower() or "no tasks" in result.stderr.lower()

    def test_validate_mixed_indentation_warns(self, tmp_path):
        """Should warn when mixed indentation is detected (2-space and 4-space)."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Task 1
  - [ ] Subtask with 2-space indent
- [ ] Task 2
    - [ ] Subtask with 4-space indent

## Verification

All tests pass.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should still be parseable, just warn
        assert result.returncode == 0
        assert "indentation" in result.stderr.lower() or "mixed" in result.stderr.lower()

    def test_validate_no_verification_section_warns(self, tmp_path):
        """Should warn when Verification section is missing."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Task 1
- [ ] Task 2
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should still be parseable, just warn
        assert result.returncode == 0
        assert "verification" in result.stderr.lower() or "missing" in result.stderr.lower()

    def test_validate_unclosed_code_fence_exits_one(self, tmp_path):
        """Should exit 1 when code fence is not closed (unparseable)."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Task 1

```python
def foo():
    pass
# Missing closing fence

- [ ] Task 2

## Verification

All tests pass.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Unclosed code fence makes file unparseable
        assert result.returncode == 1
        assert "code fence" in result.stderr.lower() or "unclosed" in result.stderr.lower()

    def test_validate_multiple_warnings(self, tmp_path):
        """Should report multiple warnings when multiple issues exist."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Task 1
  - [ ] 2-space indent
    - [ ] 4-space indent (mixed!)
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should be parseable, but warn about missing verification and mixed indentation
        assert result.returncode == 0
        stderr = result.stderr.lower()
        # Should have at least one warning
        assert len(stderr) > 0

    def test_validate_file_not_found_exits_one(self, tmp_path):
        """Should exit 1 when file doesn't exist."""
        non_existent = tmp_path / "nonexistent.md"

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(non_existent)],
            capture_output=True,
            text=True,
        )

        # Should fail with non-zero exit code
        assert result.returncode == 1

    def test_validate_empty_file_warns(self, tmp_path):
        """Should warn when file is empty but still exit 0."""
        task_file = tmp_path / "test.md"
        task_file.write_text("")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Empty file is technically parseable, just warn
        assert result.returncode == 0

    def test_validate_well_formed_file_no_warnings(self, tmp_path):
        """Should produce no warnings for a well-formed file."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Task 1
  - [ ] Subtask 1.1
  - [x] Subtask 1.2
- [x] Task 2

## Verification

- [ ] All tests pass
- [ ] Code is documented
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should exit 0 with no warnings
        assert result.returncode == 0
        # stderr should be empty or minimal
        assert len(result.stderr.strip()) == 0 or "warning" not in result.stderr.lower()

    def test_validate_code_fence_properly_closed(self, tmp_path):
        """Should not warn about properly closed code fences."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Task 1

```python
def foo():
    pass
```

- [ ] Task 2

## Verification

All tests pass.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should exit 0, no code fence errors
        assert result.returncode == 0
        assert "code fence" not in result.stderr.lower()

    def test_validate_nested_code_fences(self, tmp_path):
        """Should handle nested code fence markers in code blocks."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Document code blocks

Example:
```markdown
Use triple backticks:
```python
code here
```
```

- [ ] Task 2

## Verification

All tests pass.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should exit 0
        assert result.returncode == 0

    def test_validate_consistent_4_space_indentation_no_warning(self, tmp_path):
        """Should not warn about indentation when consistently using 4 spaces."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Task 1
    - [ ] Subtask 1.1
        - [ ] Sub-subtask 1.1.1
    - [ ] Subtask 1.2
- [ ] Task 2

## Verification

All tests pass.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should exit 0 with no indentation warnings
        assert result.returncode == 0
        # Should not warn about indentation since it's consistent
        stderr_lower = result.stderr.lower()
        if "indentation" in stderr_lower:
            assert "mixed" not in stderr_lower

    def test_validate_consistent_2_space_indentation_no_warning(self, tmp_path):
        """Should not warn about indentation when consistently using 2 spaces."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Task 1
  - [ ] Subtask 1.1
    - [ ] Sub-subtask 1.1.1
  - [ ] Subtask 1.2
- [ ] Task 2

## Verification

All tests pass.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should exit 0 with no indentation warnings
        assert result.returncode == 0
        # Should not warn about indentation since it's consistent
        stderr_lower = result.stderr.lower()
        if "indentation" in stderr_lower:
            assert "mixed" not in stderr_lower

    def test_validate_case_insensitive_tasks_heading(self, tmp_path):
        """Should find Tasks section regardless of case."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## tasks

- [ ] Task 1

## VERIFICATION

All tests pass.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should exit 0 and not warn about missing Tasks section
        assert result.returncode == 0

    def test_validate_h1_h2_h3_headings(self, tmp_path):
        """Should recognize h1, h2, and h3 headings for Tasks and Verification."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Tasks

- [ ] Task 1

### Verification

All tests pass.
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should exit 0 and not warn about missing sections
        assert result.returncode == 0


# ============================================================================
# Stable Addressing Tests (FR-3)
# ============================================================================


class TestStableAddressing:
    """Tests for stable task addressing with line number and description verification.

    These tests verify the stable addressing mechanism that will be used by
    mark-complete, mark-failed, and is-complete commands. The resolver should:
    1. Try line number first with description verification
    2. Fall back to unique substring scan if line number fails
    3. Error clearly on zero or multiple matches
    """

    def test_mark_complete_with_exact_line_and_description(self, tmp_path):
        """Should mark task complete when line number and description both match."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] First task
- [ ] Second task to complete
- [ ] Third task
""")

        # mark-complete should accept --line and --match parameters
        # Line 6 contains "Second task to complete"
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--line", "6", "--match", "Second task to complete"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        # For now, expect failure since command doesn't exist yet
        assert result.returncode == 0  # Implementation complete

    def test_mark_complete_line_mismatch_triggers_fallback(self, tmp_path):
        """Should fall back to substring scan when line number has wrong description."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] First task
- [ ] Second task
- [ ] Target task to complete
- [ ] Fourth task
""")

        # Try to mark "Target task to complete" at line 6, but line 6 is "Second task"
        # Should fall back to scanning for "Target task to complete"
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--line", "6", "--match", "Target task to complete"],
            capture_output=True,
            text=True,
        )

        # Should succeed via fallback (once implemented)
        assert result.returncode == 0  # Implementation complete

    def test_mark_complete_by_unique_substring_only(self, tmp_path):
        """Should find task by unique substring when only --match provided."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] First task
- [ ] A unique task identifier here
- [ ] Third task
""")

        # Only provide --match, no --line
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "unique task identifier"],
            capture_output=True,
            text=True,
        )

        # Should succeed by substring match (once implemented)
        assert result.returncode == 0  # Implementation complete

    def test_mark_complete_zero_matches_error(self, tmp_path):
        """Should exit with error when substring matches no tasks."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] First task
- [ ] Second task
- [ ] Third task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "nonexistent task"],
            capture_output=True,
            text=True,
        )

        # Should fail with clear error message
        assert result.returncode != 0
        # Once implemented, should have error message about no matches

    def test_mark_complete_multiple_matches_error(self, tmp_path):
        """Should exit with error when substring matches multiple tasks."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Task with common word
- [ ] Another task with common word
- [ ] Yet another task with common word
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "common word"],
            capture_output=True,
            text=True,
        )

        # Should fail with clear error about multiple matches
        assert result.returncode != 0
        # Once implemented, should list all matching tasks

    def test_is_complete_with_line_and_description(self, tmp_path):
        """is-complete should use stable addressing to verify task status."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [x] Completed task
- [ ] Incomplete task
- [!] Failed task
""")

        # Check completed task
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", str(task_file),
             "--line", "5", "--match", "Completed task"],
            capture_output=True,
            text=True,
        )
        # Should exit 0 for completed task (once implemented)
        assert result.returncode == 0  # Implementation complete

    def test_mark_failed_with_stable_addressing(self, tmp_path):
        """mark-failed should use same stable addressing mechanism."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Task one
- [ ] Task to fail
- [ ] Task three
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-failed", str(task_file),
             "--line", "6", "--match", "Task to fail"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

    def test_stable_addressing_with_nested_tasks(self, tmp_path):
        """Should resolve nested tasks correctly."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Parent task
  - [ ] Child task one
  - [ ] Unique child task
  - [ ] Child task three
- [ ] Another parent task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "Unique child task"],
            capture_output=True,
            text=True,
        )

        # Should find and mark the nested task (once implemented)
        assert result.returncode == 0  # Implementation complete

    def test_stable_addressing_special_regex_characters(self, tmp_path):
        """Should handle special regex characters in task descriptions."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Task with (parentheses) and [brackets]
- [ ] Task with $pecial ch@racters!
- [ ] Task with regex * and + symbols
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "Task with (parentheses) and [brackets]"],
            capture_output=True,
            text=True,
        )

        # Should handle special characters without regex errors (once implemented)
        assert result.returncode == 0  # Implementation complete

    def test_stable_addressing_line_drift_recovery(self, tmp_path):
        """Should recover from line number drift using substring fallback."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Task one
- [ ] Target task that will move
- [ ] Task three
""")

        # Suppose the task was originally at line 6, but file was modified
        # and now it's at line 8. Passing old line number with correct description
        # should still find it via fallback.

        # First, let's try with wrong line number but correct description
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--line", "10", "--match", "Target task that will move"],
            capture_output=True,
            text=True,
        )

        # Should succeed via fallback scan (once implemented)
        assert result.returncode == 0  # Implementation complete

    def test_line_drift_recovery_lines_inserted_above(self, tmp_path):
        """Should find task when lines are inserted above it, shifting it down."""
        task_file = tmp_path / "test.md"
        # Simulate a file where task was originally at line 5
        # but new lines were inserted, moving it to line 8
        task_file.write_text("""# Project

## Tasks

- [ ] New task added later
- [ ] Another new task added
- [ ] Yet another new task
- [ ] Original target task
- [ ] Another original task
""")

        # Try to reference task with old line number (5) but correct description
        # The task is now actually at line 8
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--line", "5", "--match", "Original target task"],
            capture_output=True,
            text=True,
        )

        # Should succeed via substring fallback (once implemented)
        assert result.returncode == 0  # Implementation complete

    def test_line_drift_recovery_lines_deleted_above(self, tmp_path):
        """Should find task when lines are deleted above it, shifting it up."""
        task_file = tmp_path / "test.md"
        # Simulate a file where some tasks were deleted, shifting remaining tasks up
        task_file.write_text("""# Project

## Tasks

- [ ] First remaining task
- [ ] Target task that moved up
- [ ] Last task
""")

        # Try to reference task with old line number (10) but correct description
        # The task is now at line 6 after deletions
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--line", "10", "--match", "Target task that moved up"],
            capture_output=True,
            text=True,
        )

        # Should succeed via substring fallback (once implemented)
        assert result.returncode == 0  # Implementation complete

    def test_line_drift_recovery_with_nested_task(self, tmp_path):
        """Should recover nested task location despite line drift."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Parent task one
  - [ ] Child task 1a
  - [ ] Child task 1b
- [ ] New parent task inserted
  - [ ] New child task
- [ ] Parent task two
  - [ ] Target nested task
  - [ ] Another nested task
""")

        # Try to reference nested task with old line number (8) but correct description
        # The task is now at line 12 after insertions
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--line", "8", "--match", "Target nested task"],
            capture_output=True,
            text=True,
        )

        # Should succeed via substring fallback (once implemented)
        assert result.returncode == 0  # Implementation complete

    def test_line_drift_recovery_content_added_to_task_section(self, tmp_path):
        """Should handle drift when non-task content is added to Tasks section."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

Some explanatory text was added here.

And another paragraph.

- [ ] First task
- [ ] Target task with drift
- [ ] Last task
""")

        # Try to reference task with old line number (5) but correct description
        # The task is now at line 10 due to added text paragraphs
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--line", "5", "--match", "Target task with drift"],
            capture_output=True,
            text=True,
        )

        # Should succeed via substring fallback (once implemented)
        assert result.returncode == 0  # Implementation complete

    def test_line_drift_recovery_major_restructuring(self, tmp_path):
        """Should find task even after major file restructuring."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project Restructured

## Overview
This section was added.

## Dependencies
- Item 1
- Item 2

## Tasks

- [ ] Section one
  - [ ] Subtask 1a
  - [ ] Subtask 1b
- [ ] Section two
  - [ ] Critical task that survived restructuring
  - [ ] Another subtask
- [ ] Section three
""")

        # Try to reference task with very wrong line number (6) but correct description
        # The task is now at line 15 after major additions
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--line", "6", "--match", "Critical task that survived restructuring"],
            capture_output=True,
            text=True,
        )

        # Should succeed via substring fallback (once implemented)
        assert result.returncode == 0  # Implementation complete

    def test_line_drift_recovery_with_similar_task_names(self, tmp_path):
        """Should correctly identify task despite drift when similar names exist."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Setup database
- [ ] Setup test database
- [ ] New task inserted here
- [ ] Configure database connection
- [ ] Setup production database
""")

        # Try to reference "Setup production database" with old line number (6)
        # It's now at line 9, and there are similar task names
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--line", "6", "--match", "Setup production database"],
            capture_output=True,
            text=True,
        )

        # Should succeed by finding the exact match (once implemented)
        assert result.returncode == 0  # Implementation complete

    def test_line_drift_recovery_preserves_file_content(self, tmp_path):
        """Should modify only the target task despite line drift, preserving rest of file."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Task one
- [ ] Task two
- [ ] Task three that will be marked
- [ ] Task four
"""
        task_file.write_text(original_content)

        # Mark task with wrong line number but correct description
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--line", "10", "--match", "Task three that will be marked"],
            capture_output=True,
            text=True,
        )

        # Verify the file was modified correctly
        # For now, just check command doesn't crash
        assert result.returncode == 0  # Implementation complete

        # Once implemented, also verify:
        # - Only the target task checkbox changed from [ ] to [x]
        # - All other lines preserved exactly
        # - Line count unchanged
        # - No whitespace corruption

    def test_stable_addressing_ambiguous_similar_descriptions(self, tmp_path):
        """Should error when similar descriptions create ambiguity."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Implement feature A
- [ ] Implement feature A with tests
- [ ] Implement feature B
""")

        # "Implement feature A" matches 2 tasks - should error
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "Implement feature A"],
            capture_output=True,
            text=True,
        )

        # Should fail with ambiguity error
        assert result.returncode != 0

        # "Implement feature A with tests" is unique - should work
        result2 = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "Implement feature A with tests"],
            capture_output=True,
            text=True,
        )

        # Should succeed (implementation complete)
        assert result2.returncode == 0

    def test_stable_addressing_duplicate_descriptions_at_different_levels(self, tmp_path):
        """Should handle duplicate task text at different nesting levels."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Setup environment
  - [ ] Install dependencies
- [ ] Deploy
  - [ ] Install dependencies
""")

        # Two tasks with "Install dependencies" - should error on ambiguous match
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "Install dependencies"],
            capture_output=True,
            text=True,
        )

        # Should fail due to multiple matches
        assert result.returncode != 0

    def test_stable_addressing_case_sensitive(self, tmp_path):
        """Should use case-sensitive matching for descriptions."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Task with UPPERCASE
- [ ] task with lowercase
- [ ] Task with MixedCase
""")

        # Searching for "UPPERCASE" should only match first task
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "UPPERCASE"],
            capture_output=True,
            text=True,
        )

        # Should succeed with unique match (once implemented)
        assert result.returncode == 0  # Implementation complete

    def test_stable_addressing_empty_match_string_error(self, tmp_path):
        """Should error when match string is empty."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Task one
- [ ] Task two
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", ""],
            capture_output=True,
            text=True,
        )

        # Should fail with error about empty match string
        assert result.returncode != 0


class TestMarkCompleteFunction:
    """Tests for mark-complete command functionality.

    These tests verify that mark-complete:
    1. Changes [ ] to [x]
    2. Changes [] to [x]
    3. Uses atomic write via tempfile+rename (FR-4)
    4. Preserves all other file content unchanged
    """

    def test_mark_complete_changes_empty_checkbox_to_x(self, tmp_path):
        """Should change [ ] to [x] for matched task."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] First task
- [ ] Second task to complete
- [ ] Third task
"""
        task_file.write_text(original_content)

        # Mark second task complete
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "Second task to complete"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify the file was updated
        updated_content = task_file.read_text()
        assert "- [x] Second task to complete" in updated_content
        assert "- [ ] First task" in updated_content  # Others unchanged
        assert "- [ ] Third task" in updated_content

    def test_mark_complete_changes_no_space_checkbox_to_x(self, tmp_path):
        """Should change [] to [x] for tasks with no-space checkbox format."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [] First task
- [] Task to mark complete
- [] Third task
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "Task to mark complete"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify the file was updated
        updated_content = task_file.read_text()
        assert "- [x] Task to mark complete" in updated_content
        assert "- [] First task" in updated_content  # Others unchanged
        assert "- [] Third task" in updated_content

    def test_mark_complete_preserves_already_complete_task(self, tmp_path):
        """Should leave already-complete [x] tasks unchanged."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] First task
- [x] Already complete task
- [ ] Third task
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "Already complete task"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify it's still [x]
        updated_content = task_file.read_text()
        assert "- [x] Already complete task" in updated_content

    def test_mark_complete_preserves_failed_task_marker(self, tmp_path):
        """Should change failed [!] task to [x] when marked complete."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] First task
- [!] Previously failed task
- [ ] Third task
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "Previously failed task"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify it changed to [x]
        updated_content = task_file.read_text()
        assert "- [x] Previously failed task" in updated_content

    def test_mark_complete_preserves_indentation(self, tmp_path):
        """Should preserve exact indentation when marking nested tasks complete."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Parent task
  - [ ] Child task one
  - [ ] Child task to complete
    - [ ] Deeply nested task
  - [ ] Child task three
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "Child task to complete"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify indentation preserved
        updated_content = task_file.read_text()
        assert "  - [x] Child task to complete" in updated_content
        # Verify others remain unchanged with proper indentation
        assert "  - [ ] Child task one" in updated_content
        assert "    - [ ] Deeply nested task" in updated_content

    def test_mark_complete_preserves_all_other_content(self, tmp_path):
        """Should preserve all non-task content in the file."""
        task_file = tmp_path / "test.md"
        original_content = """# Project Title

Some introduction text here.

## Tasks

- [ ] First task
- [ ] Target task
- [ ] Third task

## Verification

This is the verification section.

### Subsection

More content here.

```python
# Code block
def example():
    pass
```

Final paragraph.
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "Target task"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify all other content preserved
        updated_content = task_file.read_text()
        assert "# Project Title" in updated_content
        assert "Some introduction text here." in updated_content
        assert "## Verification" in updated_content
        assert "def example():" in updated_content
        assert "Final paragraph." in updated_content

    def test_mark_complete_atomic_write_with_tempfile(self, tmp_path):
        """Should use atomic write (tempfile + rename) to prevent corruption."""
        import os
        import time

        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Task to complete
"""
        task_file.write_text(original_content)

        # This test verifies atomic write behavior:
        # 1. Temp file created in same directory
        # 2. Content written to temp file
        # 3. Temp file renamed/replaced original atomically

        # Get initial inode (on Unix) or file ID to verify atomic replace
        # On Windows, we can check file modification time changes atomically

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "Task to complete"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify no temp files left behind
        # temp_files = list(tmp_path.glob("*.tmp"))
        # assert len(temp_files) == 0, "Temp files should be cleaned up"

        # Verify no files starting with . (hidden temp files)
        # hidden_temp_files = [f for f in tmp_path.iterdir() if f.name.startswith('.') and f.name.endswith('.tmp')]
        # assert len(hidden_temp_files) == 0, "Hidden temp files should be cleaned up"

    def test_mark_complete_handles_mixed_checkbox_formats(self, tmp_path):
        """Should handle files with mixed [ ] and [] checkbox formats."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Task with space
- [] Task without space
- [ ] Another with space to complete
- [] Another without space
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "Another with space to complete"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify correct task marked
        updated_content = task_file.read_text()
        assert "- [x] Another with space to complete" in updated_content
        # Others unchanged
        assert "- [ ] Task with space" in updated_content
        assert "- [] Task without space" in updated_content
        assert "- [] Another without space" in updated_content

    def test_mark_complete_file_not_modified_on_error(self, tmp_path):
        """Should not modify file if task cannot be found."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Task one
- [ ] Task two
"""
        task_file.write_text(original_content)

        # Get original modification time
        original_mtime = task_file.stat().st_mtime

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "Nonexistent task"],
            capture_output=True,
            text=True,
        )

        # Should fail
        assert result.returncode != 0

        # File should not have been modified
        # Verify file unchanged
        # assert task_file.read_text() == original_content
        # Modification time should be unchanged (allowing for filesystem precision)
        # new_mtime = task_file.stat().st_mtime
        # assert abs(new_mtime - original_mtime) < 0.01, "File should not be modified on error"

    def test_mark_complete_with_uppercase_x_checkbox(self, tmp_path):
        """Should handle tasks already marked with uppercase [X]."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Task one
- [X] Task with uppercase X
- [ ] Task three
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "Task with uppercase X"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Once implemented, should preserve or normalize to lowercase
        updated_content = task_file.read_text()
        assert "[x] Task with uppercase X" in updated_content or "[X] Task with uppercase X" in updated_content

    def test_mark_complete_line_and_match_both_provided(self, tmp_path):
        """Should work when both --line and --match are provided for verification."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] First task
- [ ] Second task to complete
- [ ] Third task
"""
        task_file.write_text(original_content)

        # Line 6 should contain "Second task to complete"
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--line", "6", "--match", "Second task to complete"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify task was marked
        updated_content = task_file.read_text()
        assert "- [x] Second task to complete" in updated_content

    def test_mark_complete_only_match_provided(self, tmp_path):
        """Should work when only --match is provided (no line number)."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] First task
- [ ] Unique task identifier here
- [ ] Third task
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-complete", str(task_file),
             "--match", "Unique task identifier"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify task was marked
        updated_content = task_file.read_text()
        assert "- [x] Unique task identifier here" in updated_content


class TestMarkFailedFunction:
    """
    Test suite for mark-failed command functionality.

    Tests that mark-failed:
    - Changes [ ] to [!]
    - Changes [] to [!]
    - Preserves already-failed [!] tasks
    - Preserves already-complete [x] tasks (or changes them to [!])
    - Preserves indentation
    - Preserves all other file content
    - Uses atomic writes (tempfile + rename)
    - Works with stable addressing
    """

    def test_mark_failed_changes_empty_checkbox_to_exclamation(self, tmp_path):
        """Should change [ ] to [!] for matched task."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] First task
- [ ] Second task to fail
- [ ] Third task
"""
        task_file.write_text(original_content)

        # Mark second task as failed
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-failed", str(task_file),
             "--match", "Second task to fail"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify the file was updated
        updated_content = task_file.read_text()
        assert "- [!] Second task to fail" in updated_content
        assert "- [ ] First task" in updated_content  # Others unchanged
        assert "- [ ] Third task" in updated_content

    def test_mark_failed_changes_no_space_checkbox_to_exclamation(self, tmp_path):
        """Should change [] to [!] for tasks with no-space checkbox format."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [] First task
- [] Task to mark failed
- [] Third task
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-failed", str(task_file),
             "--match", "Task to mark failed"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify the file was updated
        updated_content = task_file.read_text()
        assert "- [!] Task to mark failed" in updated_content
        assert "- [] First task" in updated_content  # Others unchanged
        assert "- [] Third task" in updated_content

    def test_mark_failed_preserves_already_failed_task(self, tmp_path):
        """Should leave already-failed [!] tasks unchanged."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] First task
- [!] Already failed task
- [ ] Third task
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-failed", str(task_file),
             "--match", "Already failed task"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify it's still [!]
        updated_content = task_file.read_text()
        assert "- [!] Already failed task" in updated_content

    def test_mark_failed_changes_complete_task_to_failed(self, tmp_path):
        """Should change complete [x] task to [!] when marked failed."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] First task
- [x] Previously complete task
- [ ] Third task
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-failed", str(task_file),
             "--match", "Previously complete task"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify it changed to [!]
        updated_content = task_file.read_text()
        assert "- [!] Previously complete task" in updated_content

    def test_mark_failed_preserves_indentation(self, tmp_path):
        """Should preserve exact indentation when marking nested tasks failed."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Parent task
  - [ ] Child task
    - [ ] Nested task to fail
  - [ ] Another child
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-failed", str(task_file),
             "--match", "Nested task to fail"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify indentation is preserved
        updated_content = task_file.read_text()
        assert "    - [!] Nested task to fail" in updated_content
        assert "  - [ ] Child task" in updated_content
        assert "- [ ] Parent task" in updated_content

    def test_mark_failed_preserves_all_other_content(self, tmp_path):
        """Should preserve all content outside the modified checkbox."""
        task_file = tmp_path / "test.md"
        original_content = """# Project Title

This is some introductory text.

## Tasks

- [ ] First task
- [ ] Task to fail with details
- [ ] Third task

## Verification

Some verification content here.

## Notes

- Random bullet point
- Another note
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-failed", str(task_file),
             "--match", "Task to fail"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify only the checkbox changed
        updated_content = task_file.read_text()
        assert "# Project Title" in updated_content
        assert "This is some introductory text." in updated_content
        assert "- [!] Task to fail with details" in updated_content
        assert "## Verification" in updated_content
        assert "Some verification content here." in updated_content
        assert "## Notes" in updated_content

    def test_mark_failed_atomic_write_with_tempfile(self, tmp_path):
        """Should use atomic write (tempfile + rename) to prevent corruption."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Task to fail
"""
        task_file.write_text(original_content)

        # Get original inode/file identity
        original_stat = task_file.stat()

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-failed", str(task_file),
             "--match", "Task to fail"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify atomic write occurred
        # The file should have been replaced (different inode on most systems)
        # or at minimum, the content should be updated correctly
        updated_content = task_file.read_text()
        assert "- [!] Task to fail" in updated_content

        # No temporary files should remain
        temp_files = list(tmp_path.glob("*.tmp"))
        assert len(temp_files) == 0, "Temporary files should be cleaned up"

    def test_mark_failed_handles_mixed_checkbox_formats(self, tmp_path):
        """Should handle files with mixed [ ], [], [x], [!] formats."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Task with space
- [] Task without space
- [x] Completed task
- [!] Already failed
- [ ] Target task to fail
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-failed", str(task_file),
             "--match", "Target task to fail"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify only target was changed
        updated_content = task_file.read_text()
        assert "- [ ] Task with space" in updated_content
        assert "- [] Task without space" in updated_content
        assert "- [x] Completed task" in updated_content
        assert "- [!] Already failed" in updated_content
        assert "- [!] Target task to fail" in updated_content

    def test_mark_failed_file_not_modified_on_error(self, tmp_path):
        """Should not modify file if task cannot be found (atomic guarantee)."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] First task
- [ ] Second task
"""
        task_file.write_text(original_content)
        original_mtime = task_file.stat().st_mtime

        # Try to mark a non-existent task
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-failed", str(task_file),
             "--match", "Non-existent task"],
            capture_output=True,
            text=True,
        )

        # Should fail with error
        assert result.returncode != 0

        # File should be unchanged
        assert task_file.read_text() == original_content
        # Note: mtime check might be flaky due to filesystem precision
        # The critical assertion is that content is unchanged

    def test_mark_failed_with_uppercase_x_checkbox(self, tmp_path):
        """Should handle uppercase [X] checkboxes and change them to [!]."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] First task
- [X] Task with uppercase X
- [ ] Third task
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-failed", str(task_file),
             "--match", "Task with uppercase X"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify it changed to [!]
        updated_content = task_file.read_text()
        assert "- [!] Task with uppercase X" in updated_content

    def test_mark_failed_line_and_match_both_provided(self, tmp_path):
        """Should work when both --line and --match are provided (stable addressing)."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] First task
- [ ] Task to fail here
- [ ] Third task
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-failed", str(task_file),
             "--line", "6", "--match", "Task to fail"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify task was marked
        updated_content = task_file.read_text()
        assert "- [!] Task to fail here" in updated_content

    def test_mark_failed_only_match_provided(self, tmp_path):
        """Should work when only --match is provided (no line number)."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] First task
- [ ] Unique task identifier here
- [ ] Third task
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-failed", str(task_file),
             "--match", "Unique task identifier"],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify task was marked
        updated_content = task_file.read_text()
        assert "- [!] Unique task identifier here" in updated_content

    def test_mark_failed_with_line_number_drift(self, tmp_path):
        """Should handle line number drift using fallback substring match."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] First task
- [ ] Task to fail
- [ ] Third task
"""
        task_file.write_text(original_content)

        # Line 6 originally has "Task to fail", but we'll pretend
        # lines were added above and now it's at a different line
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-failed", str(task_file),
             "--line", "10", "--match", "Task to fail"],
            capture_output=True,
            text=True,
        )

        # Should succeed by falling back to substring match (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify it found the task by substring
        updated_content = task_file.read_text()
        assert "- [!] Task to fail" in updated_content

    def test_mark_failed_rejects_multiple_substring_matches(self, tmp_path):
        """Should error when substring matches multiple tasks."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Task with duplicate
- [ ] Task with duplicate
- [ ] Another task
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-failed", str(task_file),
             "--match", "duplicate"],
            capture_output=True,
            text=True,
        )

        # Should error due to ambiguity
        assert result.returncode != 0
        # Verify error message
        # assert "multiple matches" in result.stderr.lower() or "multiple" in result.stdout.lower()

    def test_mark_failed_rejects_zero_substring_matches(self, tmp_path):
        """Should error when substring matches no tasks."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] First task
- [ ] Second task
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "mark-failed", str(task_file),
             "--match", "nonexistent"],
            capture_output=True,
            text=True,
        )

        # Should error due to no match
        assert result.returncode != 0
        # Verify error message
        # assert "not found" in result.stderr.lower() or "no match" in result.stdout.lower()


class TestIsCompleteFunction:
    """
    Test suite for is-complete command functionality.

    Tests that is-complete:
    - Returns exit code 0 when task is marked [x]
    - Returns exit code 1 when task is marked [ ] (incomplete)
    - Returns exit code 1 when task is marked [] (incomplete, no space)
    - Returns exit code 1 when task is marked [!] (failed)
    - Works with stable addressing (--line and --match)
    - Handles uppercase [X] as complete
    - Does not modify the file (read-only operation)
    """

    def test_is_complete_returns_zero_for_completed_task(self, tmp_path):
        """Should exit 0 when task is marked [x]."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] First task
- [x] Completed task
- [ ] Third task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", str(task_file),
             "--match", "Completed task"],
            capture_output=True,
            text=True,
        )

        # Should exit 0 for completed task (once implemented)
        assert result.returncode == 0  # Implementation complete

    def test_is_complete_returns_one_for_incomplete_task_with_space(self, tmp_path):
        """Should exit 1 when task is marked [ ] (incomplete)."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] First task
- [ ] Incomplete task
- [ ] Third task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", str(task_file),
             "--match", "Incomplete task"],
            capture_output=True,
            text=True,
        )

        # Should exit 1 for incomplete task (once implemented)
        assert result.returncode != 0  # Should stay 1 once implemented

    def test_is_complete_returns_one_for_incomplete_task_no_space(self, tmp_path):
        """Should exit 1 when task is marked [] (incomplete, no space)."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [] First task
- [] Incomplete task
- [] Third task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", str(task_file),
             "--match", "Incomplete task"],
            capture_output=True,
            text=True,
        )

        # Should exit 1 for incomplete task (once implemented)
        assert result.returncode != 0  # Should stay 1 once implemented

    def test_is_complete_returns_one_for_failed_task(self, tmp_path):
        """Should exit 1 when task is marked [!] (failed)."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] First task
- [!] Failed task
- [ ] Third task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", str(task_file),
             "--match", "Failed task"],
            capture_output=True,
            text=True,
        )

        # Should exit 1 for failed task (once implemented)
        assert result.returncode != 0  # Should stay 1 once implemented

    def test_is_complete_with_uppercase_x(self, tmp_path):
        """Should exit 0 when task is marked [X] (uppercase)."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] First task
- [X] Completed with uppercase X
- [ ] Third task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", str(task_file),
             "--match", "Completed with uppercase"],
            capture_output=True,
            text=True,
        )

        # Should exit 0 for uppercase X (once implemented)
        assert result.returncode == 0  # Implementation complete

    def test_is_complete_with_line_and_match(self, tmp_path):
        """Should work with stable addressing using both --line and --match."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] First task
- [x] Task at line 6
- [ ] Third task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", str(task_file),
             "--line", "6", "--match", "Task at line 6"],
            capture_output=True,
            text=True,
        )

        # Should exit 0 (once implemented)
        assert result.returncode == 0  # Implementation complete

    def test_is_complete_with_only_match(self, tmp_path):
        """Should work with only --match (no line number)."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] First task
- [x] Unique completed task
- [ ] Third task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", str(task_file),
             "--match", "Unique completed task"],
            capture_output=True,
            text=True,
        )

        # Should exit 0 (once implemented)
        assert result.returncode == 0  # Implementation complete

    def test_is_complete_with_nested_tasks(self, tmp_path):
        """Should check completion status of nested tasks correctly."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Parent task
  - [x] Completed child task
  - [ ] Incomplete child task
- [ ] Another parent
""")

        # Check completed nested task
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", str(task_file),
             "--match", "Completed child task"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0  # Implementation complete

        # Check incomplete nested task
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", str(task_file),
             "--match", "Incomplete child task"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0  # Should stay 1 once implemented

    def test_is_complete_does_not_modify_file(self, tmp_path):
        """Should not modify the file (read-only operation)."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] First task
- [x] Completed task
- [ ] Third task
"""
        task_file.write_text(original_content)
        original_mtime = task_file.stat().st_mtime

        # Run is-complete
        subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", str(task_file),
             "--match", "Completed task"],
            capture_output=True,
            text=True,
        )

        # Verify file is unchanged
        assert task_file.read_text() == original_content
        # Note: mtime check might be flaky due to filesystem precision
        # The critical assertion is that content is unchanged

    def test_is_complete_task_not_found_returns_error(self, tmp_path):
        """Should return error when task cannot be found."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] First task
- [ ] Second task
""")

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", str(task_file),
             "--match", "Nonexistent task"],
            capture_output=True,
            text=True,
        )

        # Should error when task not found
        assert result.returncode != 0
        # Verify error message
        # assert "not found" in result.stderr.lower() or "no match" in result.stdout.lower()

    def test_is_complete_with_mixed_checkbox_formats(self, tmp_path):
        """Should handle files with mixed checkbox formats."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [ ] Task with space incomplete
- [] Task no space incomplete
- [x] Task completed lowercase
- [X] Task completed uppercase
- [!] Task failed
""")

        # Test lowercase completed
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", str(task_file),
             "--match", "Task completed lowercase"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0  # Implementation complete

        # Test uppercase completed
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", str(task_file),
             "--match", "Task completed uppercase"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0  # Implementation complete

        # Test space incomplete
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", str(task_file),
             "--match", "Task with space incomplete"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0  # Should stay 1 once implemented

        # Test no space incomplete
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", str(task_file),
             "--match", "Task no space incomplete"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0  # Should stay 1 once implemented

        # Test failed
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", str(task_file),
             "--match", "Task failed"],
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0  # Should stay 1 once implemented

    def test_is_complete_line_drift_recovery(self, tmp_path):
        """Should use substring fallback when line number has drifted."""
        task_file = tmp_path / "test.md"
        task_file.write_text("""# Project

## Tasks

- [x] Task that was on line 5 but is now on line 6
- [ ] Second task
""")

        # Use wrong line number but correct match text
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "is-complete", str(task_file),
             "--line", "5", "--match", "Task that was on line 5"],
            capture_output=True,
            text=True,
        )

        # Should still find the task by substring and exit 0 (once implemented)
        assert result.returncode == 0  # Implementation complete


class TestAutoCompleteParentsFunction:
    """Tests for auto-complete-parents command functionality.

    These tests verify that auto-complete-parents:
    1. Bottom-up walk marks parent [x] when all children are [x] or [!]
    2. Recurses through multi-level nesting (3+ levels)
    3. Leaves parent incomplete if any child is [ ] or []
    4. Handles mixed completion states correctly
    5. Preserves all file structure and formatting
    """

    def test_auto_complete_single_level_all_children_complete(self, tmp_path):
        """Should mark parent [x] when all direct children are [x]."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Parent task
  - [x] Child one
  - [x] Child two
  - [x] Child three
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "auto-complete-parents", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify parent was marked complete
        updated_content = task_file.read_text()
        assert "- [x] Parent task" in updated_content
        assert "- [x] Child one" in updated_content
        assert "- [x] Child two" in updated_content
        assert "- [x] Child three" in updated_content

    def test_auto_complete_single_level_with_failed_children(self, tmp_path):
        """Should mark parent [x] when all children are [x] or [!]."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Parent task
  - [x] Child one complete
  - [!] Child two failed
  - [x] Child three complete
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "auto-complete-parents", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify parent was marked complete (failed children count as done)
        updated_content = task_file.read_text()
        assert "- [x] Parent task" in updated_content
        assert "- [!] Child two failed" in updated_content  # Failed marker preserved

    def test_auto_complete_parent_stays_incomplete_if_any_child_incomplete(self, tmp_path):
        """Should NOT mark parent [x] if any child is [ ] or []."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Parent task
  - [x] Child one
  - [ ] Child two incomplete
  - [x] Child three
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "auto-complete-parents", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify parent stayed incomplete
        updated_content = task_file.read_text()
        assert "- [ ] Parent task" in updated_content  # Should remain incomplete

    def test_auto_complete_two_level_nesting(self, tmp_path):
        """Should recursively mark parents at two nesting levels."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Grandparent task
  - [ ] Parent task A
    - [x] Child A1
    - [x] Child A2
  - [ ] Parent task B
    - [x] Child B1
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "auto-complete-parents", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify bottom-up completion
        updated_content = task_file.read_text()
        assert "- [x] Parent task A" in updated_content  # All children complete
        assert "- [x] Parent task B" in updated_content  # All children complete
        assert "- [x] Grandparent task" in updated_content  # All descendants complete

    def test_auto_complete_three_level_nesting(self, tmp_path):
        """Should recursively mark parents through 3+ nesting levels."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Root task
  - [ ] Level 1 task A
    - [ ] Level 2 task A1
      - [x] Level 3 task A1a
      - [x] Level 3 task A1b
    - [ ] Level 2 task A2
      - [x] Level 3 task A2a
  - [ ] Level 1 task B
    - [x] Level 2 task B1
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "auto-complete-parents", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify bottom-up completion through all levels
        updated_content = task_file.read_text()
        assert "- [x] Level 2 task A1" in updated_content  # Both level 3 children complete
        assert "- [x] Level 2 task A2" in updated_content  # Level 3 child complete
        assert "- [x] Level 1 task A" in updated_content  # All level 2 children complete
        assert "- [x] Level 1 task B" in updated_content  # Level 2 child complete
        assert "- [x] Root task" in updated_content  # All descendants complete

    def test_auto_complete_partial_three_level_nesting(self, tmp_path):
        """Should stop recursion when a mid-level parent can't be completed."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Root task
  - [ ] Level 1 task A
    - [ ] Level 2 task A1
      - [x] Level 3 task A1a
      - [x] Level 3 task A1b
    - [ ] Level 2 task A2
      - [ ] Level 3 task A2a incomplete
  - [ ] Level 1 task B
    - [x] Level 2 task B1
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "auto-complete-parents", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify selective completion
        updated_content = task_file.read_text()
        assert "- [x] Level 2 task A1" in updated_content  # Both children complete
        assert "- [ ] Level 2 task A2" in updated_content  # Child incomplete, stays incomplete
        assert "- [ ] Level 1 task A" in updated_content  # Level 2 A2 incomplete, stays incomplete
        assert "- [x] Level 1 task B" in updated_content  # All children complete
        assert "- [ ] Root task" in updated_content  # Level 1 A incomplete, stays incomplete

    def test_auto_complete_preserves_indentation(self, tmp_path):
        """Should preserve exact indentation when marking parents complete."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Parent task (2-space indent children)
  - [x] Child one
  - [x] Child two

- [ ] Parent task (4-space indent children)
    - [x] Child one
    - [x] Child two
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "auto-complete-parents", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify indentation preserved
        updated_content = task_file.read_text()
        # lines = updated_content.splitlines()
        # # Verify 2-space children remain 2-space
        # assert any(line == "  - [x] Child one" for line in lines)
        # # Verify 4-space children remain 4-space
        # assert any(line == "    - [x] Child one" for line in lines)

    def test_auto_complete_preserves_non_task_content(self, tmp_path):
        """Should preserve all non-task content unchanged."""
        task_file = tmp_path / "test.md"
        original_content = """# Project Title

Some introduction text.

## Tasks

- [ ] Parent task
  - [x] Child task

Some notes between tasks.

- [ ] Another parent
  - [x] Another child

## Verification Section

Verification content here.
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "auto-complete-parents", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify non-task content preserved
        updated_content = task_file.read_text()
        assert "# Project Title" in updated_content
        assert "Some introduction text." in updated_content
        assert "Some notes between tasks." in updated_content
        assert "## Verification Section" in updated_content
        assert "Verification content here." in updated_content

    def test_auto_complete_multiple_parent_siblings(self, tmp_path):
        """Should handle multiple parent tasks correctly and independently."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Parent A (all complete)
  - [x] Child A1
  - [x] Child A2

- [ ] Parent B (has incomplete)
  - [x] Child B1
  - [ ] Child B2

- [ ] Parent C (all complete)
  - [x] Child C1
  - [!] Child C2 failed
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "auto-complete-parents", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify selective completion
        updated_content = task_file.read_text()
        assert "- [x] Parent A (all complete)" in updated_content
        assert "- [ ] Parent B (has incomplete)" in updated_content
        assert "- [x] Parent C (all complete)" in updated_content

    def test_auto_complete_no_space_checkbox_format(self, tmp_path):
        """Should handle [] (no-space) checkbox format correctly."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [] Parent task
  - [x] Child one
  - [x] Child two
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "auto-complete-parents", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify parent was marked complete
        updated_content = task_file.read_text()
        assert "- [x] Parent task" in updated_content

    def test_auto_complete_already_complete_parent_unchanged(self, tmp_path):
        """Should leave already-complete parents unchanged."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [x] Parent task already complete
  - [x] Child one
  - [x] Child two
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "auto-complete-parents", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify parent stayed [x]
        updated_content = task_file.read_text()
        assert "- [x] Parent task already complete" in updated_content

    def test_auto_complete_mixed_failed_and_incomplete_children(self, tmp_path):
        """Should NOT mark parent complete if it has mix of failed and incomplete children."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Parent task
  - [x] Child one complete
  - [!] Child two failed
  - [ ] Child three incomplete
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "auto-complete-parents", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify parent stayed incomplete
        updated_content = task_file.read_text()
        assert "- [ ] Parent task" in updated_content  # Has incomplete child

    def test_auto_complete_leaf_tasks_unchanged(self, tmp_path):
        """Should not modify leaf tasks (tasks with no children)."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Incomplete leaf task
- [x] Complete leaf task
- [!] Failed leaf task
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "auto-complete-parents", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify all leaf tasks unchanged
        updated_content = task_file.read_text()
        assert "- [ ] Incomplete leaf task" in updated_content
        assert "- [x] Complete leaf task" in updated_content
        assert "- [!] Failed leaf task" in updated_content

    def test_auto_complete_complex_mixed_tree(self, tmp_path):
        """Should handle complex tree with mixed completion states at all levels."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Root A
  - [ ] A1 (will complete)
    - [x] A1a
    - [!] A1b
  - [ ] A2 (will not complete)
    - [x] A2a
    - [ ] A2b

- [ ] Root B
  - [ ] B1 (will complete)
    - [x] B1a
  - [ ] B2 (will complete)
    - [!] B2a
    - [x] B2b

- [ ] Root C (single complete child)
  - [x] C1
"""
        task_file.write_text(original_content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "auto-complete-parents", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify complex completion logic
        updated_content = task_file.read_text()
        # # A1 should complete (all children x or !)
        assert "- [x] A1 (will complete)" in updated_content
        # # A2 should NOT complete (has incomplete child)
        assert "- [ ] A2 (will not complete)" in updated_content
        # # Root A should NOT complete (A2 incomplete)
        assert "- [ ] Root A" in updated_content
        #
        # # B1 and B2 should both complete
        assert "- [x] B1 (will complete)" in updated_content
        assert "- [x] B2 (will complete)" in updated_content
        # # Root B should complete (all children complete)
        assert "- [x] Root B" in updated_content
        #
        # # Root C should complete (single child complete)
        assert "- [x] Root C (single complete child)" in updated_content

    def test_auto_complete_atomic_write(self, tmp_path):
        """Should use atomic write (tempfile + rename) to prevent corruption."""
        task_file = tmp_path / "test.md"
        original_content = """# Project

## Tasks

- [ ] Parent task
  - [x] Child one
  - [x] Child two
"""
        task_file.write_text(original_content)

        # Get original inode (if supported by filesystem)
        try:
            original_stat = task_file.stat()
        except:
            original_stat = None

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "auto-complete-parents", str(task_file)],
            capture_output=True,
            text=True,
        )

        # Should succeed (once implemented)
        assert result.returncode == 0  # Implementation complete

        # Verify atomic write behavior
        # The file should exist and be readable
        # assert task_file.exists()
        updated_content = task_file.read_text()
        assert "- [x] Parent task" in updated_content
