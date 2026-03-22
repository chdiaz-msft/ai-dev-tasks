---
description: 
globs: 
alwaysApply: false
---
# Rule: Generating a Task List from a PRD

## Goal

To guide an AI assistant in creating a detailed, step-by-step task list in Markdown format based on an existing Product Requirements Document (PRD). The task list should guide a developer through implementation using **Test-Driven Development (TDD)** — tests are always written before the corresponding implementation code.

## Output

- **Format:** Markdown (`.md`)
- **Location:** `/tasks/`
- **Filename:** `tasks-[prd-file-name].md` (e.g., `tasks-prd-user-profile-editing.md`)

## Process

1.  **Receive PRD Reference:** The user points the AI to a specific PRD file
2.  **Analyze PRD:** The AI reads and analyzes the functional requirements, user stories, and other sections of the specified PRD.
3.  **Phase 1: Generate Parent Tasks:** Based on the PRD analysis, create the file and generate the main, high-level tasks required to implement the feature. Use your judgement on how many high-level tasks to use. It's likely to be about 5. Present these tasks to the user in the specified format (without sub-tasks yet). Inform the user: "I have generated the high-level tasks based on the PRD. Ready to generate the sub-tasks? Respond with 'Go' to proceed."
4.  **Wait for Confirmation:** Pause and wait for the user to respond with "Go".
5.  **Phase 2: Generate Sub-Tasks:** Once the user confirms, break down each parent task into smaller, actionable sub-tasks necessary to complete the parent task. **Follow a TDD approach: for each parent task, generate the test-writing sub-tasks first, then the implementation sub-tasks.** The test sub-tasks should define the expected behavior (inputs, outputs, edge cases) derived from the PRD before any implementation code is written. Ensure sub-tasks logically follow from the parent task and cover the implementation details implied by the PRD.
6.  **Identify Relevant Files:** Based on the tasks and PRD, identify potential files that will need to be created or modified. List these under the `Relevant Files` section, including corresponding test files if applicable.
7.  **Generate Verification Criteria:** Based on the PRD's acceptance criteria and the tasks generated, write a `## Verification Criteria` section at the bottom of the document. Each criterion should be a concrete, observable check (e.g., a command to run, a scenario to test, or a behavior to confirm) that proves the feature works as specified. Use checkboxes (`- [ ]`) so they can be marked off during review.
8.  **Generate Final Output:** Combine the parent tasks, sub-tasks, relevant files, notes, and verification criteria into the final Markdown structure.
9.  **Save Task List:** Save the generated document in the `/tasks/` directory with the filename `tasks-[prd-file-name].md`, where `[prd-file-name]` matches the base name of the input PRD file (e.g., if the input was `prd-user-profile-editing.md`, the output is `tasks-prd-user-profile-editing.md`).

## Output Format

The generated task list _must_ follow this structure:

```markdown
## Relevant Files

- `path/to/potential/file1.ts` - Brief description of why this file is relevant (e.g., Contains the main component for this feature).
- `path/to/file1.test.ts` - Unit tests for `file1.ts`.
- `path/to/another/file.tsx` - Brief description (e.g., API route handler for data submission).
- `path/to/another/file.test.tsx` - Unit tests for `another/file.tsx`.
- `lib/utils/helpers.ts` - Brief description (e.g., Utility functions needed for calculations).
- `lib/utils/helpers.test.ts` - Unit tests for `helpers.ts`.

### Notes

- **This project follows Test-Driven Development (TDD).** For each feature or component, write the tests first, verify they fail, then write the implementation to make them pass.
- Unit tests should typically be placed alongside the code files they are testing (e.g., `MyComponent.tsx` and `MyComponent.test.tsx` in the same directory).
- Use `npx jest [optional/path/to/test/file]` to run tests. Running without a path executes all tests found by the Jest configuration.

## Tasks

- [ ] 1.0 Parent Task Title
  - [ ] 1.1 Write tests for [feature/component] (define expected behavior, inputs, outputs, and edge cases)
  - [ ] 1.2 Run tests to confirm they fail (red phase)
  - [ ] 1.3 Implement [feature/component] to make tests pass (green phase)
  - [ ] 1.4 Refactor if needed while keeping tests green
- [ ] 2.0 Parent Task Title
  - [ ] 2.1 Write tests for [feature/component]
  - [ ] 2.2 Implement [feature/component] to make tests pass
- [ ] 3.0 Parent Task Title (may not require sub-tasks if purely structural or configuration)

## Verification Criteria

How we know the tasks have been successfully implemented:

- [ ] [Concrete, observable check derived from the PRD — e.g., "Run a specific scenario end-to-end and confirm expected output"]
- [ ] [Test suite command and expected result — e.g., "Run `uv run pytest path/to/tests/` and confirm all tests pass"]
- [ ] [Any additional acceptance criteria from the PRD that can be manually or automatically verified]
```

## Interaction Model

The process explicitly requires a pause after generating parent tasks to get user confirmation ("Go") before proceeding to generate the detailed sub-tasks. This ensures the high-level plan aligns with user expectations before diving into details.

## TDD Workflow

Each parent task should follow the **Red-Green-Refactor** cycle:

1. **Red:** Write tests that define the expected behavior based on the PRD. Run them to confirm they fail.
2. **Green:** Write the minimum implementation code to make the tests pass.
3. **Refactor:** Clean up the implementation while ensuring all tests remain green.

Sub-tasks within each parent task must be ordered so that **all test-writing sub-tasks come before any implementation sub-tasks**. This ensures the developer never writes implementation code without a failing test to guide it.

## Target Audience

Assume the primary reader of the task list is a **junior developer** who will implement the feature.
