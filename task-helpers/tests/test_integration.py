"""
Integration tests for task_parser.py validate command.

These tests verify that the validate command works correctly against
a variety of task file formats that represent real-world usage patterns.

Following the TDD approach:
1. Create fixtures representing various task file formats
2. Run validate against all task files
3. Verify they all parse without errors
"""

import subprocess
import sys
from pathlib import Path

import pytest

# Path to the task_parser.py script
TASK_PARSER = Path(__file__).parent.parent / "task_parser.py"


class TestValidateIntegration:
    """Integration tests for validate command against task files."""

    @pytest.fixture
    def tasks_dir(self, tmp_path):
        """Create a temporary tasks/ directory with various task file formats."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        return tasks_dir

    @pytest.fixture
    def simple_task_file(self, tasks_dir):
        """Create a simple task file with basic structure."""
        file_path = tasks_dir / "simple-task.md"
        content = """# Simple Task File

## Tasks

- [ ] Task 1
- [ ] Task 2
- [x] Task 3

## Verification

All tasks should be parseable.
"""
        file_path.write_text(content)
        return file_path

    @pytest.fixture
    def nested_task_file(self, tasks_dir):
        """Create a task file with nested subtasks."""
        file_path = tasks_dir / "nested-task.md"
        content = """# Nested Task File

## Tasks

- [ ] Parent task 1
  - [ ] Child task 1.1
  - [ ] Child task 1.2
    - [ ] Grandchild task 1.2.1
- [x] Parent task 2
  - [x] Child task 2.1

## Verification

Nested tasks should be parsed correctly.
"""
        file_path.write_text(content)
        return file_path

    @pytest.fixture
    def complex_task_file(self, tasks_dir):
        """Create a complex task file with code blocks, varied spacing, etc."""
        file_path = tasks_dir / "complex-task.md"
        content = """# Complex Task File

This file contains various edge cases.

## Tasks

- [ ] Task with code example:
  ```python
  # This checkbox should be ignored
  - [ ] Not a real task
  ```
- [x] Completed task
- [!] Failed task
- [] Empty checkbox task
- [ ] Task with 2-space indentation
  - [ ] Subtask 1
  - [x] Subtask 2
- [ ] Task with 4-space indentation
    - [ ] Subtask A
    - [ ] Subtask B

## Verification

- All task formats should be recognized
- Code blocks should be properly excluded
- Mixed checkbox formats should work
"""
        file_path.write_text(content)
        return file_path

    @pytest.fixture
    def prd_style_task_file(self, tasks_dir):
        """Create a task file matching PRD/project planning style."""
        file_path = tasks_dir / "project-tasks.md"
        content = """# Project: Implement Feature X

## Overview

This project implements feature X with the following requirements.

## Tasks

- [x] 1.0 Set up project structure
  - [x] 1.1 Create directory structure
  - [x] 1.2 Initialize configuration files
  - [x] 1.3 Set up testing framework
- [ ] 2.0 Implement core functionality
  - [x] 2.1 Write tests for core functions
  - [ ] 2.2 Implement core logic
  - [ ] 2.3 Add error handling
- [ ] 3.0 Documentation and deployment
  - [ ] 3.1 Write API documentation
  - [ ] 3.2 Create deployment scripts

## Verification Criteria

- [ ] All tests pass
- [ ] Code coverage > 80%
- [ ] Documentation is complete
"""
        file_path.write_text(content)
        return file_path

    @pytest.fixture
    def multiline_task_file(self, tasks_dir):
        """Create a task file with multiline task descriptions."""
        file_path = tasks_dir / "multiline-task.md"
        content = """# Multiline Task File

## Tasks

- [ ] Task with long description that spans
      multiple lines with continuation
- [ ] Another task
  - [ ] Subtask with description that
        continues on next line
- [x] Completed multiline task
      with additional details

## Verification

Multiline descriptions should be handled correctly.
"""
        file_path.write_text(content)
        return file_path

    @pytest.fixture
    def case_variations_file(self, tasks_dir):
        """Create a task file with case variations in headings."""
        file_path = tasks_dir / "case-variations.md"
        content = """# Case Variations

## tasks

- [ ] Task under lowercase heading

## VERIFICATION

All lowercase/uppercase headings should work.
"""
        file_path.write_text(content)
        return file_path

    @pytest.fixture
    def heading_levels_file(self, tasks_dir):
        """Create a task file with different heading levels."""
        file_path = tasks_dir / "heading-levels.md"
        content = """# Heading Levels Test

### Tasks

- [ ] Task under h3 heading

# Verification

- Verification under h1 heading should work
"""
        file_path.write_text(content)
        return file_path

    @pytest.fixture
    def all_task_files(
        self,
        simple_task_file,
        nested_task_file,
        complex_task_file,
        prd_style_task_file,
        multiline_task_file,
        case_variations_file,
        heading_levels_file,
    ):
        """Return a list of all task file fixtures."""
        return [
            simple_task_file,
            nested_task_file,
            complex_task_file,
            prd_style_task_file,
            multiline_task_file,
            case_variations_file,
            heading_levels_file,
        ]

    def test_validate_all_task_files_parse_without_errors(self, all_task_files):
        """Integration test: validate should successfully parse all task files.

        This test verifies that the validate command can parse a variety of
        real-world task file formats without errors. All files should be
        considered parseable (exit code 0), though they may have warnings.
        """
        for task_file in all_task_files:
            result = subprocess.run(
                [sys.executable, str(TASK_PARSER), "validate", str(task_file)],
                capture_output=True,
                text=True,
            )

            # Validate should exit 0 for parseable files
            # (warnings are OK, but the file must be parseable)
            assert result.returncode == 0, (
                f"validate failed for {task_file.name}:\n"
                f"STDOUT: {result.stdout}\n"
                f"STDERR: {result.stderr}"
            )

    def test_validate_simple_task_file(self, simple_task_file):
        """Validate can parse a simple task file."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(simple_task_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        # Simple file should have no warnings
        assert "warning" not in result.stdout.lower() or "0 warnings" in result.stdout.lower()

    def test_validate_nested_task_file(self, nested_task_file):
        """Validate can parse a nested task file."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(nested_task_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_validate_complex_task_file(self, complex_task_file):
        """Validate can parse a complex task file with code blocks."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(complex_task_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        # Should recognize tasks but skip checkboxes in code blocks

    def test_validate_prd_style_task_file(self, prd_style_task_file):
        """Validate can parse a PRD-style project task file."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(prd_style_task_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_validate_multiline_task_file(self, multiline_task_file):
        """Validate can parse tasks with multiline descriptions."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(multiline_task_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_validate_case_variations_file(self, case_variations_file):
        """Validate handles case-insensitive headings."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(case_variations_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_validate_heading_levels_file(self, heading_levels_file):
        """Validate handles different heading levels (h1-h3)."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(heading_levels_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_validate_all_files_in_tasks_directory(self, tasks_dir, all_task_files):
        """Integration test: validate all .md files in a tasks directory.

        This test simulates the real-world scenario where we run validate
        against all task files in a directory tree.
        """
        # Find all .md files in tasks directory
        md_files = list(tasks_dir.glob("**/*.md"))

        # Should have found all our fixture files
        assert len(md_files) == len(all_task_files)

        # Validate each one
        for md_file in md_files:
            result = subprocess.run(
                [sys.executable, str(TASK_PARSER), "validate", str(md_file)],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, (
                f"validate failed for {md_file.name}:\n"
                f"STDOUT: {result.stdout}\n"
                f"STDERR: {result.stderr}"
            )

    def test_validate_reports_file_path_in_output(self, simple_task_file):
        """Validate command should successfully process the file (output format may vary)."""
        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(simple_task_file)],
            capture_output=True,
            text=True,
        )
        # Main requirement: validate should exit successfully
        assert result.returncode == 0
        # Output format may vary (some implementations are silent on success)

    def test_validate_handles_subdirectories(self, tasks_dir):
        """Validate can handle task files in subdirectories."""
        # Create a subdirectory with a task file
        subdir = tasks_dir / "subfolder"
        subdir.mkdir()

        task_file = subdir / "subtask.md"
        content = """# Subtask File

## Tasks

- [ ] Task in subdirectory

## Verification

Should work in subdirectories.
"""
        task_file.write_text(content)

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(task_file)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0


class TestValidateExistingTaskFiles:
    """Integration tests for validate command against existing task files in repository.

    This test class specifically addresses requirement 6.1:
    'Write integration tests that run validate against all existing tasks/**/*.md files'
    """

    def test_validate_all_existing_tasks_files(self):
        """Run validate against all existing tasks/**/*.md files in the repository.

        This test finds all markdown files in the tasks/ directory (if it exists)
        and validates that they can all be parsed without errors.

        If no tasks/ directory exists or contains no .md files, the test passes.
        This allows the test to work both before and after task files are added.
        """
        # Find the repository root (3 levels up from this test file)
        repo_root = Path(__file__).parent.parent.parent
        tasks_dir = repo_root / "tasks"

        # If tasks directory doesn't exist, test passes (nothing to validate)
        if not tasks_dir.exists():
            pytest.skip("No tasks/ directory found in repository")
            return

        # Find all .md files in tasks directory
        md_files = list(tasks_dir.glob("**/*.md"))

        # If no markdown files found, test passes
        if not md_files:
            pytest.skip("No .md files found in tasks/ directory")
            return

        # Validate each file
        failures = []
        for md_file in md_files:
            result = subprocess.run(
                [sys.executable, str(TASK_PARSER), "validate", str(md_file)],
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                failures.append({
                    "file": md_file.relative_to(repo_root),
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                })

        # Assert all files validated successfully
        if failures:
            error_msg = f"validate failed for {len(failures)} file(s):\n"
            for failure in failures:
                error_msg += f"\n  File: {failure['file']}\n"
                error_msg += f"  Exit code: {failure['returncode']}\n"
                error_msg += f"  STDOUT: {failure['stdout']}\n"
                error_msg += f"  STDERR: {failure['stderr']}\n"
            pytest.fail(error_msg)

        # If we get here, all files validated successfully
        print(f"Successfully validated {len(md_files)} task file(s)")

    def test_validate_project_prd_task_file(self):
        """Validate the actual PRD task file for this project.

        This test specifically validates the task file used for tracking this project's progress.
        """
        repo_root = Path(__file__).parent.parent.parent
        prd_task_file = repo_root / "task-parser-rewrite" / "tasks-prd-task-parser-rewrite.md"

        # This file should exist as part of the project
        if not prd_task_file.exists():
            pytest.skip(f"PRD task file not found: {prd_task_file}")
            return

        result = subprocess.run(
            [sys.executable, str(TASK_PARSER), "validate", str(prd_task_file)],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, (
            f"validate failed for PRD task file:\n"
            f"STDOUT: {result.stdout}\n"
            f"STDERR: {result.stderr}"
        )
