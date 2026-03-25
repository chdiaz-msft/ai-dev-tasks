"""
Integration tests comparing bash v1 functions to Python CLI output.

These tests verify that the Python CLI implementation produces identical
results to the original bash functions from ralph-wiggum.sh.

Test approach:
1. Create task files with various structures
2. Call bash functions directly (by sourcing ralph-wiggum.sh)
3. Call Python CLI commands
4. Compare outputs to ensure parity

Following TDD:
- Write tests first
- Run to verify they fail (red phase)
- Implement fixes if needed (green phase)
"""

import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest


# Path to the scripts
TASK_PARSER = Path(__file__).parent.parent / "task_parser.py"
RALPH_V1_SCRIPT = Path(__file__).parent.parent.parent / "ralph-wiggum.sh"


def call_bash_function(function_name: str, task_file: Path) -> tuple[int, str, str]:
    """
    Call a bash function from ralph-wiggum.sh.

    Returns (returncode, stdout, stderr) tuple.

    Uses bash_function_wrapper.sh to extract and call functions
    without triggering ralph-wiggum.sh's argument parsing logic.
    """
    wrapper_script = Path(__file__).parent / "bash_function_wrapper.sh"

    # Convert Windows paths to Unix-style paths for WSL/Git Bash/MSYS2
    # Detect which bash we're using by checking pwd output
    pwd_result = subprocess.run(
        ["bash", "-c", "pwd"],
        capture_output=True,
        text=True,
    )
    uses_mnt = "/mnt/" in pwd_result.stdout

    def win_to_unix_path(path: Path) -> str:
        path_str = str(path).replace('\\', '/')
        # Convert drive letter (C:/ -> /mnt/c/ or /c/ depending on bash flavor)
        if len(path_str) > 2 and path_str[1] == ':':
            drive = path_str[0].lower()
            rest = path_str[2:]
            if uses_mnt:
                path_str = f"/mnt/{drive}{rest}"
            else:
                path_str = f"/{drive}{rest}"
        return path_str

    task_file_str = win_to_unix_path(task_file)
    wrapper_path_str = win_to_unix_path(wrapper_script)

    # Call wrapper script with absolute paths
    result = subprocess.run(
        ["bash", wrapper_path_str, function_name, task_file_str],
        capture_output=True,
        text=True,
    )

    return (result.returncode, result.stdout, result.stderr)


def call_python_cli(command: str, task_file: Path, extra_args: list[str] = None) -> tuple[int, str, str]:
    """
    Call the Python CLI with the specified command.

    Returns (returncode, stdout, stderr) tuple.
    """
    args = [sys.executable, str(TASK_PARSER), command, str(task_file)]
    if extra_args:
        args.extend(extra_args)

    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
    )

    return (result.returncode, result.stdout, result.stderr)


class TestBashPythonParity:
    """Integration tests comparing bash and Python implementations."""

    @pytest.fixture
    def simple_task_file(self, tmp_path):
        """Create a simple task file for testing."""
        file_path = tmp_path / "simple-tasks.md"
        content = """# Simple Task File

## Tasks

- [ ] First incomplete task
- [x] Second task (complete)
- [ ] Third incomplete task
- [!] Fourth task (failed)

## Verification

All tasks should be correctly identified.
"""
        file_path.write_text(content)
        return file_path

    @pytest.fixture
    def nested_task_file(self, tmp_path):
        """Create a nested task file for testing."""
        file_path = tmp_path / "nested-tasks.md"
        content = """# Nested Task File

## Tasks

- [x] 1.0 Parent task one
  - [x] 1.1 Child task one
  - [x] 1.2 Child task two
- [ ] 2.0 Parent task two
  - [x] 2.1 Child task one
  - [ ] 2.2 Child task two (next!)
  - [ ] 2.3 Child task three
- [ ] 3.0 Parent task three
  - [ ] 3.1 Child task one

## Verification

Should correctly identify next leaf task.
"""
        file_path.write_text(content)
        return file_path

    @pytest.fixture
    def complex_nested_file(self, tmp_path):
        """Create a complex nested task file with multiple levels."""
        file_path = tmp_path / "complex-tasks.md"
        content = """# Complex Task File

## Tasks

- [x] 1.0 Completed parent
  - [x] 1.1 Completed child
  - [x] 1.2 Another completed child
    - [x] 1.2.1 Completed grandchild
- [ ] 2.0 In-progress parent
  - [x] 2.1 Completed child
  - [ ] 2.2 In-progress child
    - [x] 2.2.1 Completed grandchild
    - [ ] 2.2.2 Next incomplete grandchild
    - [ ] 2.2.3 Another grandchild
  - [ ] 2.3 Incomplete child
- [ ] 3.0 Not-started parent
  - [ ] 3.1 Not-started child

## Verification

Complex nesting should be handled correctly.
"""
        file_path.write_text(content)
        return file_path

    @pytest.fixture
    def all_complete_file(self, tmp_path):
        """Create a task file where all tasks are complete."""
        file_path = tmp_path / "all-complete.md"
        content = """# All Complete

## Tasks

- [x] Task one
- [x] Task two
- [x] Parent task
  - [x] Child one
  - [x] Child two

## Verification

Should report no tasks remaining.
"""
        file_path.write_text(content)
        return file_path

    @pytest.fixture
    def mixed_status_file(self, tmp_path):
        """Create a file with mixed task statuses."""
        file_path = tmp_path / "mixed-status.md"
        content = """# Mixed Status

## Tasks

- [x] Completed task
- [!] Failed task
- [ ] Incomplete task one
- [] Empty checkbox task
- [X] Uppercase X task
- [ ] Incomplete task two

## Verification

Should handle all status types.
"""
        file_path.write_text(content)
        return file_path

    def test_next_task_simple_parity(self, simple_task_file):
        """Compare get_next_task (bash) vs next-task (Python) on simple file."""
        # Call bash function
        bash_rc, bash_out, bash_err = call_bash_function("get_next_task", simple_task_file)

        # Call Python CLI
        py_rc, py_out, py_err = call_python_cli("next-task", simple_task_file)

        # Return codes should match
        assert bash_rc == py_rc, (
            f"Return codes differ: bash={bash_rc}, python={py_rc}\n"
            f"Bash output: {bash_out}\n"
            f"Bash stderr: {bash_err}\n"
            f"Python output: {py_out}"
        )

        # Output format: line_num|parent_text|task_text
        # Normalize whitespace and compare
        bash_output = bash_out.strip()
        py_output = py_out.strip()

        assert bash_output == py_output, (
            f"Output differs:\n"
            f"Bash:   '{bash_output}'\n"
            f"Python: '{py_output}'"
        )

    def test_next_task_nested_parity(self, nested_task_file):
        """Compare get_next_task (bash) vs next-task (Python) on nested file."""
        bash_rc, bash_out, bash_err = call_bash_function("get_next_task", nested_task_file)
        py_rc, py_out, py_err = call_python_cli("next-task", nested_task_file)

        assert bash_rc == py_rc

        # Parse outputs - normalize line endings (bash on Windows may output CRLF artifacts)
        # The bash output sometimes has \r\n in the middle of the pipe-delimited string
        # due to CRLF line endings in ralph-wiggum.sh when run on Windows
        bash_output = bash_out.strip().replace('\r', '').replace('\n', '')
        py_output = py_out.strip()

        # Should both identify task "2.2 Child task two (next!)"
        assert bash_output == py_output, (
            f"Output differs:\n"
            f"Bash:   '{bash_output}'\n"
            f"Python: '{py_output}'"
        )

        # Verify it's the correct task (line 10, parent "2.0 Parent task two")
        parts = py_output.split("|")
        assert len(parts) == 3
        line_num, parent, task = parts
        assert "2.2" in task or "Child task two" in task

    def test_next_task_complex_nested_parity(self, complex_nested_file):
        """Compare next-task on complex nested file with multiple levels.

        NOTE: Bash v1 has a known bug where it doesn't properly detect parent tasks
        with subtasks, treating them as leaves. Python correctly identifies the deepest
        leaf task. This test verifies both implementations run successfully but does
        not require identical output for this known bug.
        """
        bash_rc, bash_out, bash_err = call_bash_function("get_next_task", complex_nested_file)
        py_rc, py_out, py_err = call_python_cli("next-task", complex_nested_file)

        # Both should succeed (return 0)
        assert bash_rc == 0, f"Bash failed: {bash_err}"
        assert py_rc == 0, f"Python failed: {py_err}"

        # Verify Python returns the correct deepest leaf task
        py_parts = py_out.strip().split("|")
        assert len(py_parts) == 3
        assert "2.2.2" in py_parts[2], f"Python should identify grandchild task 2.2.2, got: {py_parts[2]}"

        # Note: Bash incorrectly returns "2.2 In-progress child" which is a parent task
        # This is a known limitation of the bash implementation

    def test_next_task_all_complete_parity(self, all_complete_file):
        """Compare next-task when all tasks are complete (should exit 1)."""
        bash_rc, bash_out, bash_err = call_bash_function("get_next_task", all_complete_file)
        py_rc, py_out, py_err = call_python_cli("next-task", all_complete_file)

        # Both should return non-zero (no tasks remain)
        assert bash_rc == py_rc
        assert bash_rc != 0, "Should return non-zero when no tasks remain"

    def test_count_simple_parity(self, simple_task_file):
        """Compare count_tasks (bash) vs count (Python) on simple file."""
        bash_rc, bash_out, bash_err = call_bash_function("count_tasks", simple_task_file)
        py_rc, py_out, py_err = call_python_cli("count", simple_task_file)

        # Return codes should match
        assert bash_rc == py_rc

        # Output format: total|completed|failed
        bash_output = bash_out.strip()
        py_output = py_out.strip()

        assert bash_output == py_output, (
            f"Count output differs:\n"
            f"Bash:   '{bash_output}'\n"
            f"Python: '{py_output}'"
        )

        # Verify the counts make sense (4 total, 1 complete, 1 failed)
        parts = py_output.split("|")
        assert len(parts) == 3
        total, completed, failed = map(int, parts)
        assert total == 4, f"Expected 4 total tasks, got {total}"
        assert completed == 1, f"Expected 1 completed task, got {completed}"
        assert failed == 1, f"Expected 1 failed task, got {failed}"

    def test_count_nested_parity(self, nested_task_file):
        """Compare count on nested file (should only count leaf tasks)."""
        bash_rc, bash_out, bash_err = call_bash_function("count_tasks", nested_task_file)
        py_rc, py_out, py_err = call_python_cli("count", nested_task_file)

        assert bash_rc == py_rc
        assert bash_out.strip() == py_out.strip()

        # Verify only leaf tasks are counted (not parent tasks)
        parts = py_out.strip().split("|")
        total, completed, failed = map(int, parts)
        # Should have 6 leaf tasks (1.1, 1.2, 2.1, 2.2, 2.3, 3.1)
        assert total == 6, f"Expected 6 leaf tasks, got {total}"

    def test_count_complex_nested_parity(self, complex_nested_file):
        """Compare count on complex nested file with multiple levels."""
        bash_rc, bash_out, bash_err = call_bash_function("count_tasks", complex_nested_file)
        py_rc, py_out, py_err = call_python_cli("count", complex_nested_file)

        assert bash_rc == py_rc
        assert bash_out.strip() == py_out.strip()

    def test_count_all_complete_parity(self, all_complete_file):
        """Compare count when all tasks are complete."""
        bash_rc, bash_out, bash_err = call_bash_function("count_tasks", all_complete_file)
        py_rc, py_out, py_err = call_python_cli("count", all_complete_file)

        assert bash_rc == py_rc
        assert bash_out.strip() == py_out.strip()

        # Verify all tasks are marked complete
        parts = py_out.strip().split("|")
        total, completed, failed = map(int, parts)
        assert total == completed, "All tasks should be complete"
        assert failed == 0, "No tasks should be failed"

    def test_count_mixed_status_parity(self, mixed_status_file):
        """Compare count with mixed task statuses.

        Note: Python correctly treats [X] (uppercase) as completed, while bash v1
        does not. This is an intentional improvement in the Python implementation.
        """
        bash_rc, bash_out, bash_err = call_bash_function("count_tasks", mixed_status_file)
        py_rc, py_out, py_err = call_python_cli("count", mixed_status_file)

        assert bash_rc == py_rc

        # Parse the outputs
        bash_total, bash_completed, bash_failed = map(int, bash_out.strip().split("|"))
        py_total, py_completed, py_failed = map(int, py_out.strip().split("|"))

        # Total and failed should match
        assert bash_total == py_total, f"Total count differs: bash={bash_total}, python={py_total}"
        assert bash_failed == py_failed, f"Failed count differs: bash={bash_failed}, python={py_failed}"

        # Python should count 1 more completed than bash (due to [X] uppercase support)
        # Bash v1 doesn't recognize [X] as completed, Python does
        assert py_completed == bash_completed + 1, (
            f"Python should count 1 more completed task than bash due to [X] support. "
            f"bash_completed={bash_completed}, py_completed={py_completed}"
        )

    def test_parity_on_project_prd_file(self):
        """Compare bash and Python on the actual PRD task file for this project."""
        repo_root = Path(__file__).parent.parent.parent
        prd_task_file = repo_root / "task-parser-rewrite" / "tasks-prd-task-parser-rewrite.md"

        if not prd_task_file.exists():
            pytest.skip(f"PRD task file not found: {prd_task_file}")
            return

        # Test next-task
        bash_rc, bash_out, bash_err = call_bash_function("get_next_task", prd_task_file)
        py_rc, py_out, py_err = call_python_cli("next-task", prd_task_file)

        assert bash_rc == py_rc, (
            f"next-task return codes differ on PRD file:\n"
            f"Bash RC: {bash_rc}, output: '{bash_out.strip()}'\n"
            f"Python RC: {py_rc}, output: '{py_out.strip()}'"
        )

        if bash_rc == 0:  # Only compare output if both succeeded
            # Normalize bash output for CRLF artifacts
            bash_normalized = bash_out.strip().replace('\r', '').replace('\n', '')
            py_normalized = py_out.strip()
            assert bash_normalized == py_normalized, (
                f"next-task output differs on PRD file:\n"
                f"Bash:   '{bash_normalized}'\n"
                f"Python: '{py_normalized}'"
            )

        # Test count
        bash_rc, bash_out, bash_err = call_bash_function("count_tasks", prd_task_file)
        py_rc, py_out, py_err = call_python_cli("count", prd_task_file)

        assert bash_rc == py_rc, (
            f"count return codes differ on PRD file:\n"
            f"Bash RC: {bash_rc}, output: '{bash_out.strip()}'\n"
            f"Python RC: {py_rc}, output: '{py_out.strip()}'"
        )

        # For count, we may have small differences due to uppercase X support
        # Just verify the counts are close (within 1-2 tasks due to [X] handling)
        bash_total, bash_completed, bash_failed = map(int, bash_out.strip().split("|"))
        py_total, py_completed, py_failed = map(int, py_out.strip().split("|"))

        assert bash_total == py_total, f"Total count differs: bash={bash_total}, python={py_total}"
        assert bash_failed == py_failed, f"Failed count differs: bash={bash_failed}, python={py_failed}"
        # Allow for small difference in completed count due to [X] uppercase handling
        assert abs(py_completed - bash_completed) <= 2, (
            f"Completed count differs by more than 2: bash={bash_completed}, python={py_completed}"
        )

    def test_next_task_parity_multiple_files(self, tmp_path):
        """Test next-task parity across multiple task files.

        NOTE: Bash v1 has bugs with deeply nested tasks - it treats parent tasks
        as leaves. This test verifies both succeed but only checks parity for simple
        cases where bash behavior is correct.
        """
        # Create multiple test files
        test_files = []

        # File 1: Simple sequential tasks (bash handles this correctly)
        f1 = tmp_path / "seq.md"
        f1.write_text("""## Tasks
- [x] Task A
- [ ] Task B
- [ ] Task C
## Verification
Test
""")
        test_files.append((f1, True))  # (file, check_parity)

        # File 2: Deeply nested (bash has bugs here - doesn't properly detect leaf tasks)
        f2 = tmp_path / "deep.md"
        f2.write_text("""## Tasks
- [ ] Level 0
  - [ ] Level 1
    - [ ] Level 2
      - [ ] Level 3
## Verification
Test
""")
        test_files.append((f2, False))  # Don't check parity - known bash bug

        # File 3: Mixed indentation (2-space and 4-space)
        f3 = tmp_path / "mixed.md"
        f3.write_text("""## Tasks
- [ ] Parent A
  - [x] Child with 2-space
- [ ] Parent B
    - [ ] Child with 4-space
## Verification
Test
""")
        test_files.append((f3, True))  # bash handles this correctly

        # Test each file
        for task_file, check_parity in test_files:
            bash_rc, bash_out, _ = call_bash_function("get_next_task", task_file)
            py_rc, py_out, _ = call_python_cli("next-task", task_file)

            assert bash_rc == py_rc, f"Return codes differ for {task_file.name}"

            if check_parity:
                # Normalize bash output for CRLF artifacts
                bash_normalized = bash_out.strip().replace('\r', '').replace('\n', '')
                py_normalized = py_out.strip()
                assert bash_normalized == py_normalized, (
                    f"Output differs for {task_file.name}:\n"
                    f"Bash:   '{bash_normalized}'\n"
                    f"Python: '{py_normalized}'"
                )
            else:
                # Just verify both succeeded - known bash bug means output won't match
                assert py_rc == 0, f"Python should find a task in {task_file.name}"

    def test_count_parity_multiple_files(self, tmp_path):
        """Test count parity across multiple task files."""
        # Create multiple test files with different characteristics
        test_files = []

        # File 1: All incomplete
        f1 = tmp_path / "incomplete.md"
        f1.write_text("""## Tasks
- [ ] Task 1
- [ ] Task 2
- [ ] Task 3
## Verification
Test
""")
        test_files.append(f1)

        # File 2: Mixed statuses with nesting
        f2 = tmp_path / "mixed-nested.md"
        f2.write_text("""## Tasks
- [x] Parent 1
  - [x] Child 1.1
  - [x] Child 1.2
- [!] Parent 2
  - [!] Child 2.1
- [ ] Parent 3
  - [ ] Child 3.1
  - [x] Child 3.2
## Verification
Test
""")
        test_files.append(f2)

        # Test each file
        for task_file in test_files:
            bash_rc, bash_out, _ = call_bash_function("count_tasks", task_file)
            py_rc, py_out, _ = call_python_cli("count", task_file)

            assert bash_rc == py_rc, f"Return codes differ for {task_file.name}"
            assert bash_out.strip() == py_out.strip(), (
                f"Count differs for {task_file.name}:\n"
                f"Bash:   '{bash_out.strip()}'\n"
                f"Python: '{py_out.strip()}'"
            )

    def test_edge_case_empty_parent_text(self, tmp_path):
        """Test next-task parity when parent text might be empty (top-level tasks)."""
        file_path = tmp_path / "top-level.md"
        file_path.write_text("""## Tasks
- [x] Top level complete
- [ ] Top level incomplete
## Verification
Test
""")

        bash_rc, bash_out, _ = call_bash_function("get_next_task", file_path)
        py_rc, py_out, _ = call_python_cli("next-task", file_path)

        assert bash_rc == py_rc
        assert bash_out.strip() == py_out.strip()

        # Parent text should be empty for top-level tasks
        parts = py_out.strip().split("|")
        assert len(parts) == 3
        line_num, parent, task = parts
        assert parent == "", f"Expected empty parent for top-level task, got '{parent}'"

    def test_edge_case_whitespace_variations(self, tmp_path):
        """Test parity with various whitespace patterns in checkboxes."""
        file_path = tmp_path / "whitespace.md"
        file_path.write_text("""## Tasks
- [ ] Normal spacing
- []  Extra space after checkbox
-  [ ]  Extra spaces around
- [x]Completed no space
## Verification
Test
""")

        bash_rc, bash_out, _ = call_bash_function("count_tasks", file_path)
        py_rc, py_out, _ = call_python_cli("count", file_path)

        # Both should handle whitespace variations the same way
        assert bash_rc == py_rc
        assert bash_out.strip() == py_out.strip()
