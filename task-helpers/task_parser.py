#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""
Task Parser CLI - Markdown task file parser for ralph-wiggum automation.

This tool parses markdown task files with checkbox-based task lists,
providing commands for reading task status, counting tasks, marking tasks
complete/failed, and extracting verification sections.

Provides robust Python-based task file manipulation for the Ralph Wiggum
automation harness.
"""

import argparse
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from typing import NoReturn, Optional


@dataclass
class Task:
    """Represents a single task in the task tree.

    Attributes:
        text: The task description (without checkbox syntax)
        status: Task status - "incomplete", "complete", or "failed"
        line_number: Original line number in the markdown file (1-indexed)
        indent_level: Indentation level (0 = root, 1 = first level child, etc.)
        parent: Reference to parent Task, or None for root tasks
        children: List of child Task objects
    """
    text: str
    status: str  # "incomplete", "complete", or "failed"
    line_number: int
    indent_level: int
    parent: Optional['Task'] = None
    children: list['Task'] = field(default_factory=list)


# Regex patterns for parsing
CHECKBOX_PATTERN = re.compile(r'^(\s*)- \[([ xX!]?)\] ?(.*?)$')
HEADING_PATTERN = re.compile(r'^(#{1,3})\s+(.+)$')
CODE_FENCE_PATTERN = re.compile(r'^```+|^~~~+')


def find_tasks_section(content: str) -> Optional[tuple[int, int]]:
    """Find the ## Tasks section in the markdown content.

    Returns (start_line, end_line) tuple where lines are 0-indexed,
    or None if no Tasks section is found.

    The section starts at a heading containing "tasks" (case-insensitive, h1-h3)
    and ends at the next equal-or-higher-level heading or EOF.
    """
    lines = content.splitlines()
    tasks_start = None
    tasks_heading_level = None
    in_code_block = False
    code_fence_marker = None

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Track code block state with proper fence matching
        fence_match = CODE_FENCE_PATTERN.match(stripped)
        if fence_match:
            if not in_code_block:
                in_code_block = True
                code_fence_marker = fence_match.group(0)
            elif stripped.startswith(code_fence_marker):
                in_code_block = False
                code_fence_marker = None
            continue

        if in_code_block:
            continue

        # Check for headings
        heading_match = HEADING_PATTERN.match(stripped)
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()

            # Found the Tasks section
            if tasks_start is None and title.lower() == "tasks":
                tasks_start = i
                tasks_heading_level = level
            # Found end of Tasks section (equal or higher level heading)
            elif tasks_start is not None and level <= tasks_heading_level:
                return (tasks_start, i)

    # If we found a Tasks section but no ending, it goes to EOF
    if tasks_start is not None:
        return (tasks_start, len(lines))

    return None


def is_checkbox_line(line: str) -> bool:
    """Check if a line contains a checkbox pattern."""
    return CHECKBOX_PATTERN.match(line) is not None


def get_checkbox_status(line: str) -> str:
    """Extract the status from a checkbox line.

    Returns: "incomplete", "complete", or "failed"
    """
    match = CHECKBOX_PATTERN.match(line)
    if not match:
        return "incomplete"

    checkbox_char = match.group(2)
    if checkbox_char in ('x', 'X'):
        return "complete"
    elif checkbox_char == '!':
        return "failed"
    else:
        return "incomplete"


def extract_checkbox_text(line: str) -> str:
    """Extract the task description text from a checkbox line."""
    match = CHECKBOX_PATTERN.match(line)
    if not match:
        return ""
    return match.group(3)


def get_indentation_level(line: str) -> int:
    """Get the number of leading whitespace characters."""
    match = CHECKBOX_PATTERN.match(line)
    if not match:
        return 0
    whitespace = match.group(1)
    # Count spaces and tabs
    return len(whitespace)


def calculate_indent_level(line: str, indent_size: int) -> int:
    """Calculate the indentation level based on indent_size.

    Args:
        line: The line to analyze
        indent_size: Number of spaces per indent level (2 or 4)

    Returns:
        The indentation level (0 for root, 1+ for nested)
    """
    indentation = get_indentation_level(line)
    return indentation // indent_size


def detect_indent_size(content: str) -> int:
    """Detect whether the content uses 2-space or 4-space indentation.

    Returns: 2 or 4 (defaults to 2 if unable to detect)
    """
    lines = content.splitlines()

    # Look for the first indented checkbox to determine indent size
    for line in lines:
        if is_checkbox_line(line):
            indent = get_indentation_level(line)
            if indent > 0:
                # Common indent sizes are 2 or 4
                if indent % 4 == 0:
                    return 4
                elif indent % 2 == 0:
                    return 2

    # Default to 2-space indent
    return 2


def parse_task_file(content: str) -> list[Task]:
    """Parse a markdown task file and build an in-memory tree of tasks.

    Args:
        content: The full markdown file content

    Returns:
        A list of all Task objects (both root and nested tasks).
        Use task.parent to identify root tasks (parent is None).
    """
    # Find the Tasks section
    section_range = find_tasks_section(content)
    if section_range is None:
        return []

    start_line, end_line = section_range
    lines = content.splitlines()

    # Detect indent size
    indent_size = detect_indent_size(content)

    # Track code block state within the Tasks section
    in_code_block = False
    code_fence_marker = None  # Track which fence opened the block
    all_tasks = []
    stack = []  # Stack to track parent tasks at each level

    for line_num in range(start_line, end_line):
        line = lines[line_num]
        stripped = line.strip()

        # Track code blocks with proper fence matching
        fence_match = CODE_FENCE_PATTERN.match(stripped)
        if fence_match:
            if not in_code_block:
                # Opening a code block
                in_code_block = True
                code_fence_marker = fence_match.group(0)
            elif stripped.startswith(code_fence_marker):
                # Closing the code block (must match opening fence length)
                in_code_block = False
                code_fence_marker = None
            continue

        if in_code_block:
            continue

        # Check if this is a checkbox line
        if not is_checkbox_line(line):
            continue

        # Parse the checkbox
        indent_level = calculate_indent_level(line, indent_size)
        status = get_checkbox_status(line)
        text = extract_checkbox_text(line)

        # Create the Task object
        task = Task(
            text=text,
            status=status,
            line_number=line_num + 1,  # 1-indexed for user-facing line numbers
            indent_level=indent_level,
            parent=None,
            children=[]
        )

        # Adjust the stack to the current indent level
        # Remove any tasks at deeper or equal levels
        while stack and stack[-1].indent_level >= indent_level:
            stack.pop()

        # Set parent relationship
        if stack:
            parent = stack[-1]
            task.parent = parent
            parent.children.append(task)

        # Add to stack for potential children
        stack.append(task)
        all_tasks.append(task)

    return all_tasks


def has_unclosed_code_fence(content: str) -> bool:
    """Check if the content has any unclosed code fences.

    Returns True if there's an unclosed fence, False otherwise.
    """
    lines = content.splitlines()
    in_code_block = False
    code_fence_marker = None

    for line in lines:
        stripped = line.strip()
        fence_match = CODE_FENCE_PATTERN.match(stripped)
        if fence_match:
            if not in_code_block:
                # Opening a code block
                in_code_block = True
                code_fence_marker = fence_match.group(0)
            elif stripped.startswith(code_fence_marker):
                # Closing the code block
                in_code_block = False
                code_fence_marker = None

    # If we're still in a code block at the end, it's unclosed
    return in_code_block


def has_verification_section(content: str) -> bool:
    """Check if the content has a Verification section.

    Looks for a heading (h1-h3) starting with "verif" (case-insensitive).
    """
    lines = content.splitlines()

    for line in lines:
        stripped = line.strip()
        heading_match = HEADING_PATTERN.match(stripped)
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            # Check if this is h1-h3 and starts with "verif" (case-insensitive)
            if level <= 3 and title.lower().startswith("verif"):
                return True

    return False


def has_mixed_indentation(content: str) -> bool:
    """Check if the content has mixed indentation (both 2-space and 4-space).

    Returns True if both 2-space and 4-space indentation patterns are detected.

    Strategy: Look at the actual parent-child relationships in the parsed tree.
    If siblings have different indentation, or if the indent increments are inconsistent,
    it's mixed.
    """
    lines = content.splitlines()

    # Track indentation by looking at parent-child pairs
    # Collect (parent_indent, child_indent) pairs
    indent_pairs = []
    prev_indent = None

    for line in lines:
        if is_checkbox_line(line):
            indent = get_indentation_level(line)
            if prev_indent is not None and indent > prev_indent:
                # This is a child of the previous task
                indent_diff = indent - prev_indent
                indent_pairs.append((prev_indent, indent, indent_diff))
            prev_indent = indent

    if not indent_pairs:
        # No parent-child relationships found
        return False

    # Check if indent increments are consistent
    # E.g., all increments should be 2 (for 2-space) or all 4 (for 4-space)
    indent_diffs = set(diff for _, _, diff in indent_pairs)

    # If we have both 2 and 4 as increment sizes, it's mixed
    has_2_increment = 2 in indent_diffs
    has_4_increment = 4 in indent_diffs

    if has_2_increment and has_4_increment:
        return True

    # Also check for other inconsistencies like 6, 8, etc.
    # But 2 is OK, 4 is OK, having both is NOT OK

    return False


def main() -> None:
    """Main entry point for the task parser CLI."""
    parser = argparse.ArgumentParser(
        description="Parse and manipulate markdown task files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s next-task tasks.md
  %(prog)s next-parent tasks.md
  %(prog)s count tasks.md
  %(prog)s mark-complete tasks.md 42
  %(prog)s mark-failed tasks.md "implement feature X"
  %(prog)s mark-subtree-failed tasks.md --line 42 --match "parent task"
  %(prog)s is-complete tasks.md 42
  %(prog)s is-subtree-resolved tasks.md --line 42 --match "parent task"
  %(prog)s auto-complete-parents tasks.md
  %(prog)s verification-section tasks.md
  %(prog)s validate tasks.md
        """,
    )

    subparsers = parser.add_subparsers(dest="command", required=True, help="Command to execute")

    # next-task command
    parser_next = subparsers.add_parser(
        "next-task",
        help="Get the next incomplete leaf task"
    )
    parser_next.add_argument("file", help="Path to the task file")

    # next-parent command
    parser_next_parent = subparsers.add_parser(
        "next-parent",
        help="Get the next incomplete top-level task and its full markdown subtree"
    )
    parser_next_parent.add_argument("file", help="Path to the task file")

    # count command
    parser_count = subparsers.add_parser(
        "count",
        help="Count tasks by status (total|completed|failed)"
    )
    parser_count.add_argument("file", help="Path to the task file")

    # mark-complete command
    parser_mark_complete = subparsers.add_parser(
        "mark-complete",
        help="Mark a task as complete [x]"
    )
    parser_mark_complete.add_argument("file", help="Path to the task file")
    parser_mark_complete.add_argument(
        "identifier",
        nargs="?",
        help="Task identifier (line number or unique substring)"
    )
    parser_mark_complete.add_argument(
        "--line",
        type=int,
        help="Hint: expected line number"
    )
    parser_mark_complete.add_argument(
        "--match",
        help="Hint: expected task description for verification"
    )

    # mark-failed command
    parser_mark_failed = subparsers.add_parser(
        "mark-failed",
        help="Mark a task as failed [!]"
    )
    parser_mark_failed.add_argument("file", help="Path to the task file")
    parser_mark_failed.add_argument(
        "identifier",
        nargs="?",
        help="Task identifier (line number or unique substring)"
    )
    parser_mark_failed.add_argument(
        "--line",
        type=int,
        help="Hint: expected line number"
    )
    parser_mark_failed.add_argument(
        "--match",
        help="Hint: expected task description for verification"
    )

    # mark-subtree-failed command
    parser_mark_subtree_failed = subparsers.add_parser(
        "mark-subtree-failed",
        help="Mark every incomplete task in a parent subtree as failed [!]"
    )
    parser_mark_subtree_failed.add_argument("file", help="Path to the task file")
    parser_mark_subtree_failed.add_argument(
        "--line",
        type=int,
        required=True,
        help="Expected parent task line number"
    )
    parser_mark_subtree_failed.add_argument(
        "--match",
        required=True,
        help="Expected parent task description"
    )

    # is-complete command
    parser_is_complete = subparsers.add_parser(
        "is-complete",
        help="Check if a task is marked complete (exit 0=yes, 1=no)"
    )
    parser_is_complete.add_argument("file", help="Path to the task file")
    parser_is_complete.add_argument(
        "identifier",
        nargs="?",
        help="Task identifier (line number or unique substring)"
    )
    parser_is_complete.add_argument(
        "--line",
        type=int,
        help="Hint: expected line number"
    )
    parser_is_complete.add_argument(
        "--match",
        help="Hint: expected task description for verification"
    )

    # is-subtree-resolved command
    parser_is_subtree_resolved = subparsers.add_parser(
        "is-subtree-resolved",
        help="Check whether a task subtree has no incomplete leaf tasks"
    )
    parser_is_subtree_resolved.add_argument("file", help="Path to the task file")
    parser_is_subtree_resolved.add_argument(
        "--line",
        type=int,
        required=True,
        help="Expected parent task line number"
    )
    parser_is_subtree_resolved.add_argument(
        "--match",
        required=True,
        help="Expected parent task description"
    )

    # auto-complete-parents command
    parser_auto_complete = subparsers.add_parser(
        "auto-complete-parents",
        help="Automatically mark parent tasks complete when all children are done"
    )
    parser_auto_complete.add_argument("file", help="Path to the task file")

    # verification-section command
    parser_verification = subparsers.add_parser(
        "verification-section",
        help="Extract the verification/criteria section content"
    )
    parser_verification.add_argument("file", help="Path to the task file")

    # validate command
    parser_validate = subparsers.add_parser(
        "validate",
        help="Validate task file structure and report issues"
    )
    parser_validate.add_argument("file", help="Path to the task file")

    args = parser.parse_args()

    # Dispatch to command handlers (stubs for now)
    try:
        if args.command == "next-task":
            cmd_next_task(args.file)
        elif args.command == "next-parent":
            cmd_next_parent(args.file)
        elif args.command == "count":
            cmd_count(args.file)
        elif args.command == "mark-complete":
            cmd_mark_complete(args.file, args.identifier, args.line, args.match)
        elif args.command == "mark-failed":
            cmd_mark_failed(args.file, args.identifier, args.line, args.match)
        elif args.command == "mark-subtree-failed":
            cmd_mark_subtree_failed(args.file, args.line, args.match)
        elif args.command == "is-complete":
            cmd_is_complete(args.file, args.identifier, args.line, args.match)
        elif args.command == "is-subtree-resolved":
            cmd_is_subtree_resolved(args.file, args.line, args.match)
        elif args.command == "auto-complete-parents":
            cmd_auto_complete_parents(args.file)
        elif args.command == "verification-section":
            cmd_verification_section(args.file)
        elif args.command == "validate":
            cmd_validate(args.file)
        else:
            print(f"Unknown command: {args.command}", file=sys.stderr)
            sys.exit(2)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(3)


def resolve_task(
    tasks: list[Task],
    identifier: str | None = None,
    line: int | None = None,
    match: str | None = None
) -> Task:
    """Resolve a task using stable addressing.

    Args:
        tasks: List of all tasks from parse_task_file()
        identifier: Primary identifier (line number as string or substring), optional if line/match provided
        line: Optional hint for expected line number
        match: Optional hint for expected task description

    Returns:
        The resolved Task object

    Raises:
        ValueError: If task cannot be uniquely identified

    Strategy (FR-3):
    1. If 'line' is provided, try to match that line number first
       - Verify the description matches if 'match' is also provided
    2. Otherwise (or if line number fails), try substring matching on 'identifier' or 'match'
    3. Error if zero or multiple matches found
    """
    # Try to parse identifier as an integer (line number)
    primary_line_num = None
    if identifier:
        try:
            primary_line_num = int(identifier)
        except ValueError:
            primary_line_num = None

    # Strategy 1: Try line number with optional description verification
    if line is not None or primary_line_num is not None:
        target_line = line if line is not None else primary_line_num

        # Find task at this line number
        for task in tasks:
            if task.line_number == target_line:
                # Found task at expected line number
                # If we have a match string, verify the description
                if match is not None:
                    if match in task.text:
                        return task
                    # Line number matched but description didn't - fall through to substring search
                    break
                else:
                    # No description verification needed
                    return task

        # If we get here, line number didn't work (line not found or description mismatch)
        # Fall through to substring matching

    # Strategy 2: Substring matching
    # Use 'match' if provided, otherwise use 'identifier'
    search_string = match if match is not None else identifier

    # Handle empty search string
    if not search_string or (isinstance(search_string, str) and search_string.strip() == ""):
        raise ValueError("Cannot resolve task: empty match string or no identifier provided")

    # Try to parse as line number if we haven't already
    if primary_line_num is not None and match is None and identifier:
        # identifier was a line number but we failed to find it
        raise ValueError(f"Cannot resolve task: no task found at line {primary_line_num}")

    # Search for tasks containing the substring
    matches = []
    for task in tasks:
        if search_string in task.text:
            matches.append(task)

    if len(matches) == 0:
        raise ValueError(f"Cannot resolve task: no task contains '{search_string}'")
    elif len(matches) > 1:
        raise ValueError(f"Cannot resolve task: multiple tasks ({len(matches)}) contain '{search_string}'")

    return matches[0]


def atomic_write(file_path: str, content: str) -> None:
    """Write content to file atomically using tempfile + os.replace.

    This ensures that the file is never in a partially-written state.
    If the write fails, the original file remains unchanged.

    Args:
        file_path: Path to the file to write
        content: Content to write to the file

    Strategy (FR-4):
    1. Create temporary file in the same directory as target file
    2. Write content to temporary file
    3. Atomically replace original file with temp file using os.replace()

    Raises:
        OSError: If file operations fail
    """
    # Get the directory and filename
    file_path_obj = os.path.abspath(file_path)
    directory = os.path.dirname(file_path_obj)

    # Create a temporary file in the same directory
    # Using delete=False so we can control when it's deleted
    # Using the same directory ensures atomic rename works (same filesystem)
    fd, temp_path = tempfile.mkstemp(
        dir=directory,
        prefix=".tmp_task_",
        suffix=".md",
        text=True
    )

    try:
        # Write content to the temporary file
        with os.fdopen(fd, 'w', encoding='utf-8') as temp_file:
            temp_file.write(content)

        # Atomically replace the original file with the temp file
        # os.replace() is atomic on both Unix and Windows
        os.replace(temp_path, file_path_obj)

    except Exception:
        # If anything goes wrong, clean up the temp file
        try:
            os.unlink(temp_path)
        except OSError:
            pass  # Temp file might already be gone
        raise


# Command handler stubs

def cmd_next_task(file_path: str) -> None:
    """Get the next incomplete leaf task.

    Returns pipe-delimited output: line_num|parent_text|task_text
    Exits 1 if no incomplete leaf tasks remain.
    Exits 3 if file cannot be read.
    """
    # Read the file
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(3)

    # Parse the task file
    tasks = parse_task_file(content)

    # Find the next incomplete leaf task (in document order)
    for task in tasks:
        # Skip if not incomplete
        if task.status != "incomplete":
            continue

        # Skip if not a leaf (has children)
        if task.children:
            continue

        # This is the next incomplete leaf task
        parent_text = task.parent.text if task.parent else ""
        print(f"{task.line_number}|{parent_text}|{task.text}")
        sys.exit(0)

    # No incomplete leaf tasks found
    sys.exit(1)


def cmd_next_parent(file_path: str) -> None:
    """Get the top-level task containing the next incomplete leaf task.

    The first output line is pipe-delimited metadata:
    start_line|end_line|parent_text.
    The remaining output is the complete markdown block for that top-level task,
    including prose, nested subtasks, and their current completion state.
    Exits 1 if no incomplete leaf tasks remain.
    Exits 3 if the file cannot be read.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(3)

    tasks = parse_task_file(content)
    next_leaf = next(
        (
            task
            for task in tasks
            if task.status == "incomplete" and not task.children
        ),
        None,
    )
    if next_leaf is None:
        sys.exit(1)

    parent = next_leaf
    while parent.parent is not None:
        parent = parent.parent

    section_range = find_tasks_section(content)
    if section_range is None:
        sys.exit(1)

    _, section_end = section_range
    block_end = section_end
    for task in tasks:
        if task.parent is None and task.line_number > parent.line_number:
            block_end = task.line_number - 1
            break

    lines = content.splitlines()
    block_lines = lines[parent.line_number - 1:block_end]
    while block_lines and not block_lines[-1].strip():
        block_lines.pop()
    block_end = parent.line_number + len(block_lines) - 1

    print(f"{parent.line_number}|{block_end}|{parent.text}")
    print("\n".join(block_lines))


def cmd_count(file_path: str) -> None:
    """Count tasks by status.

    Returns pipe-delimited output: total|completed|failed
    Counts only leaf tasks (tasks with no children).
    Exits 3 if file cannot be read.
    """
    # Read the file
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(3)

    # Parse the task file
    tasks = parse_task_file(content)

    # Count only leaf tasks
    total = 0
    completed = 0
    failed = 0

    for task in tasks:
        # Skip parent tasks (only count leaf tasks)
        if task.children:
            continue

        # Count this leaf task
        total += 1
        if task.status == "complete":
            completed += 1
        elif task.status == "failed":
            failed += 1

    # Print pipe-delimited output
    print(f"{total}|{completed}|{failed}")


def _mark_task_status(
    file_path: str,
    status_char: str,
    identifier: str | None = None,
    line: int | None = None,
    match: str | None = None
) -> None:
    """Helper function to mark a task with a specific status character.

    Args:
        file_path: Path to the task file
        status_char: The status character to set ('x' for complete, '!' for failed)
        identifier: Optional task identifier
        line: Optional line number hint
        match: Optional description hint
    """
    # Read the file
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(3)

    # Parse the task file
    tasks = parse_task_file(content)

    # Resolve the task using stable addressing
    try:
        task = resolve_task(tasks, identifier, line, match)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(3)

    # Modify the file to mark the task
    lines = content.splitlines(keepends=True)

    # Find the line to update (task.line_number is 1-indexed)
    target_line_idx = task.line_number - 1

    if target_line_idx >= len(lines):
        print(f"Error: Line {task.line_number} is out of range", file=sys.stderr)
        sys.exit(3)

    original_line = lines[target_line_idx]

    # Match the checkbox pattern and replace the status character
    checkbox_match = CHECKBOX_PATTERN.match(original_line.rstrip('\n\r'))
    if not checkbox_match:
        print(f"Error: Line {task.line_number} does not contain a valid checkbox", file=sys.stderr)
        sys.exit(3)

    # Build the new line with the specified status
    indent = checkbox_match.group(1)
    text = checkbox_match.group(3)
    # Preserve the original line ending
    line_ending = original_line[len(original_line.rstrip('\n\r')):]
    new_line = f"{indent}- [{status_char}] {text}{line_ending}"

    lines[target_line_idx] = new_line

    # Write the modified content atomically
    new_content = ''.join(lines)
    try:
        atomic_write(file_path, new_content)
    except Exception as e:
        print(f"Error writing file: {e}", file=sys.stderr)
        sys.exit(3)

    # Success - exit 0
    sys.exit(0)


def cmd_mark_complete(
    file_path: str,
    identifier: str | None = None,
    line: int | None = None,
    match: str | None = None
) -> None:
    """Mark a task as complete [x]."""
    _mark_task_status(file_path, 'x', identifier, line, match)


def cmd_mark_failed(
    file_path: str,
    identifier: str | None = None,
    line: int | None = None,
    match: str | None = None
) -> None:
    """Mark a task as failed [!]."""
    _mark_task_status(file_path, '!', identifier, line, match)


def cmd_mark_subtree_failed(file_path: str, line: int, match: str) -> None:
    """Mark every incomplete task in a task subtree as failed [!]."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(3)

    tasks = parse_task_file(content)
    try:
        root = resolve_task(tasks, line=line, match=match)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(3)

    def subtree_tasks(task: Task) -> list[Task]:
        return [
            task,
            *(
                descendant
                for child in task.children
                for descendant in subtree_tasks(child)
            ),
        ]

    lines = content.splitlines(keepends=True)
    for task in subtree_tasks(root):
        if task.status != "incomplete":
            continue

        target_line_idx = task.line_number - 1
        original_line = lines[target_line_idx]
        checkbox_match = CHECKBOX_PATTERN.match(original_line.rstrip('\n\r'))
        if not checkbox_match:
            print(
                f"Error: Line {task.line_number} does not contain a valid checkbox",
                file=sys.stderr,
            )
            sys.exit(3)

        indent = checkbox_match.group(1)
        text = checkbox_match.group(3)
        line_ending = original_line[len(original_line.rstrip('\n\r')):]
        lines[target_line_idx] = f"{indent}- [!] {text}{line_ending}"

    try:
        atomic_write(file_path, ''.join(lines))
    except Exception as e:
        print(f"Error writing file: {e}", file=sys.stderr)
        sys.exit(3)
    sys.exit(0)


def cmd_is_complete(
    file_path: str,
    identifier: str | None = None,
    line: int | None = None,
    match: str | None = None
) -> None:
    """Check if a task is marked complete.

    Exits 0 if task is marked [x], 1 otherwise.
    """
    # Read the file
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(3)

    # Parse the task file
    tasks = parse_task_file(content)

    # Resolve the task using stable addressing
    try:
        task = resolve_task(tasks, identifier, line, match)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(3)

    # Check if task is complete
    if task.status == "complete":
        sys.exit(0)
    else:
        sys.exit(1)


def cmd_is_subtree_resolved(file_path: str, line: int, match: str) -> None:
    """Check whether a task subtree contains no incomplete leaf tasks.

    Exits 0 when every leaf is complete or failed, 1 when any leaf remains
    incomplete, and 3 when the task cannot be resolved.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(3)

    tasks = parse_task_file(content)
    try:
        root = resolve_task(tasks, line=line, match=match)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(3)

    def leaf_tasks(task: Task) -> list[Task]:
        if not task.children:
            return [task]
        return [
            leaf
            for child in task.children
            for leaf in leaf_tasks(child)
        ]

    if any(task.status == "incomplete" for task in leaf_tasks(root)):
        sys.exit(1)
    sys.exit(0)


def cmd_auto_complete_parents(file_path: str) -> None:
    """Automatically mark parent tasks complete when all children are done.

    Bottom-up walk through the task tree:
    - Mark parent [x] when all children are [x] or [!]
    - Recurse through multi-level nesting
    - Leave parent incomplete if any child is [ ] or []

    Uses atomic file writes to ensure data integrity.
    Exits 0 on success, 3 on error.
    """
    # Read the file
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(3)

    # Parse the task file
    tasks = parse_task_file(content)

    if not tasks:
        # No tasks to process
        sys.exit(0)

    # Build a set of tasks that need to be marked complete
    # We'll track which line numbers need updates
    tasks_to_complete = set()

    # Bottom-up walk: process leaf tasks first, then work up to parents
    # We need multiple passes to cascade completion through all levels
    # Strategy: For each parent task, check if all children are complete or failed

    # Keep iterating until no more parents can be auto-completed
    # This handles multi-level nesting
    changed = True
    while changed:
        changed = False

        # Get all parent tasks
        parent_tasks = [task for task in tasks if task.children]

        # Process in reverse order (deepest nesting first) for bottom-up completion
        # Sort by indent level descending, so we process deepest tasks first
        parent_tasks.sort(key=lambda t: t.indent_level, reverse=True)

        # For each parent, check if all children are done (complete or failed)
        for parent in parent_tasks:
            if parent.status == "complete":
                # Already complete, skip
                continue

            # Skip if already marked for completion in a previous iteration
            if parent.line_number in tasks_to_complete:
                continue

            # Check if all children are complete or failed
            all_children_done = True
            for child in parent.children:
                # Check child's actual status, accounting for pending updates
                child_status = child.status
                if child.line_number in tasks_to_complete:
                    child_status = "complete"

                if child_status not in ("complete", "failed"):
                    all_children_done = False
                    break

            if all_children_done and len(parent.children) > 0:
                # Mark this parent for completion
                tasks_to_complete.add(parent.line_number)
                parent.status = "complete"  # Update in-memory status for next iteration
                changed = True

    # If no tasks need to be completed, we're done
    if not tasks_to_complete:
        sys.exit(0)

    # Update the file
    lines = content.splitlines(keepends=True)

    for line_num in tasks_to_complete:
        # line_num is 1-indexed, convert to 0-indexed
        line_idx = line_num - 1

        if line_idx >= len(lines):
            print(f"Error: Line {line_num} is out of range", file=sys.stderr)
            sys.exit(3)

        original_line = lines[line_idx]

        # Replace [ ] or [] with [x]
        checkbox_match = CHECKBOX_PATTERN.match(original_line.rstrip('\n\r'))
        if not checkbox_match:
            # This shouldn't happen if our parsing is correct
            continue

        # Build the new line with [x] status
        indent = checkbox_match.group(1)
        text = checkbox_match.group(3)
        # Preserve the original line ending
        line_ending = original_line[len(original_line.rstrip('\n\r')):]
        new_line = f"{indent}- [x] {text}{line_ending}"

        lines[line_idx] = new_line

    # Write the modified content atomically
    new_content = ''.join(lines)
    try:
        atomic_write(file_path, new_content)
    except Exception as e:
        print(f"Error writing file: {e}", file=sys.stderr)
        sys.exit(3)

    # Success
    sys.exit(0)


def cmd_verification_section(file_path: str) -> None:
    """Extract the verification/criteria section content.

    Finds a heading starting with 'verif' (case-insensitive, h1-h3),
    extracts all content until the next heading or EOF.
    Exits 3 if file cannot be read.
    """
    # Read the file
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(3)

    lines = content.splitlines()
    verification_start = None

    # Pattern to match any heading (h1-h6)
    any_heading_pattern = re.compile(r'^#{1,6}\s+(.+)$')

    # Find the verification heading (h1-h3, starts with "verif", case-insensitive)
    for i, line in enumerate(lines):
        stripped = line.strip()
        heading_match = HEADING_PATTERN.match(stripped)
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            # Check if this is h1-h3 and starts with "verif" (case-insensitive)
            if level <= 3 and title.lower().startswith("verif"):
                verification_start = i
                break

    # If no verification section found, return empty
    if verification_start is None:
        return

    # Extract content from after the verification heading to the next heading or EOF
    content_lines = []
    for i in range(verification_start + 1, len(lines)):
        line = lines[i]
        # Check if this is any heading (h1-h6)
        if any_heading_pattern.match(line.strip()):
            break
        content_lines.append(line)

    # Print the content
    print('\n'.join(content_lines))


def cmd_validate(file_path: str) -> None:
    """Validate task file structure and report issues.

    Checks for:
    - Missing Tasks section
    - No checkboxes in Tasks section
    - Mixed indentation (2-space and 4-space)
    - Missing Verification section
    - Unclosed code fences (makes file unparseable)

    Exits 0 if parseable (even with warnings), 1 if unparseable or file cannot be read.
    Warnings are written to stderr.
    """
    # Read the file
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file: {e}", file=sys.stderr)
        sys.exit(1)

    warnings = []

    # Check for unclosed code fences (makes file unparseable)
    if has_unclosed_code_fence(content):
        print("Error: Unclosed code fence detected", file=sys.stderr)
        sys.exit(1)

    # Check for Tasks section
    tasks_section = find_tasks_section(content)
    if tasks_section is None:
        warnings.append("Warning: Missing Tasks section")
    else:
        # Check for checkboxes in Tasks section
        tasks = parse_task_file(content)
        if len(tasks) == 0:
            warnings.append("Warning: No checkboxes found in Tasks section")
        else:
            # Check for mixed indentation
            if has_mixed_indentation(content):
                warnings.append("Warning: Mixed indentation detected (both 2-space and 4-space)")

    # Check for Verification section
    if not has_verification_section(content):
        warnings.append("Warning: Missing Verification section")

    # Print all warnings to stderr
    for warning in warnings:
        print(warning, file=sys.stderr)

    # Exit 0 (file is parseable, even if there are warnings)
    sys.exit(0)


if __name__ == "__main__":
    main()
