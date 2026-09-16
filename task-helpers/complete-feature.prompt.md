---
description:
globs:
alwaysApply: false
---
# Feature Completion

Run this prompt after all tasks in a feature's task list are marked `[x]`. It verifies completion, archives the feature folder, and presents changes for review.

## Steps

### 1. Verify completion

- Read the feature's task list file (`tasks/<folder>/tasks-prd-<name>.md`).
- Confirm every task and sub-task is `[x]`.
- If any are still `[ ]`, stop and tell the user which tasks remain.

### 2. Rename files with `completed-` prefix

- Within the feature folder, rename every PRD and task-list file by adding a `completed-` prefix to the filename.
  - Example: `prd-bucket-driven-input-derivation.md` → `completed-prd-bucket-driven-input-derivation.md`
  - Example: `tasks-prd-bucket-driven-input-derivation.md` → `completed-tasks-prd-bucket-driven-input-derivation.md`
- For single-file features (no folder), apply the same `completed-` prefix rename.

### 3. Move the feature folder

- Move the feature's folder from `tasks/<folder>/` to `tasks/completed/<folder>/`.
- If the feature lives in a single spec file rather than a folder, move that file into `tasks/completed/` instead.
- Create `tasks/completed/` if it does not already exist.

### 4. Update documentation

- Update any existing repository documentation directly affected by the completed feature.
- Do not create or modify unrelated documentation.

### 5. Confirm

- Run `git status` and show the user the resulting changes for review before committing.
