---
name: python-code-review
description: >
  Parallel Python code review using 5 specialized review tracks.
  Use this skill when asked to review Python code, review a Python PR,
  or perform a Python code review. Dispatches parallel subagents for
  correctness, type safety, architecture, tests, and production readiness.
---

# Python Code Review — Parallel Agent Dispatch

Split a Python code review across multiple independent subagents, each focused on a distinct concern. The review checklist lives in `python_code_review_guidelines.md` at the repository root.

## Workflow

### Step 0 — Pre-review gate

Before dispatching review agents, verify the author has completed **§0 — Pre-review gates** from the guidelines. Check:

- Formatter + linter are clean (`ruff`)
- Type checker is clean (`pyright` in strict mode)
- Tests pass (`uv run pytest`)
- All commands run through `uv`
- PR description answers: why, what, how to validate

If any gate fails, return the PR to the author before spending agent review cycles.

### Step 1 — Gather context

1. Identify the diff or file list under review.
2. Read the full `python_code_review_guidelines.md` from the repository root.

### Step 2 — Dispatch parallel review agents

Launch **5 subagents in parallel** (use the `task` tool with `agent_type: "explore"` or `agent_type: "code-review"`). Each agent receives:

- The diff / file list under review
- The full `python_code_review_guidelines.md` content
- Its track definition (below)
- The instruction: **"Review ONLY your assigned sections. Output a structured list of findings with file, line, severity (Blocker / Suggestion), and explanation."**

---

#### Track 1 — Correctness, Contracts & Safety

**Focus:** Does the code do what it claims, fail safely, and avoid security holes?

**Assigned sections from the guidelines:**
- **§A** — Correctness & contracts
- **§C** — Defaults & fail-fast
- **§D** — Error handling & resilience
- **§I** — Security & secrets

---

#### Track 2 — Type System & Modeling

**Focus:** Is the type system pulling its weight? Are domain concepts modeled precisely?

**Assigned sections from the guidelines:**
- **§B** — Type safety & modeling (all subsections: `Any` avoidance, rich models, constrained generics, type system lapses, nullability, immutability, primitive obsession)

---

#### Track 3 — Architecture & Design

**Focus:** Is the code well-structured, properly layered, and easy to understand?

**Assigned sections from the guidelines:**
- **§E** — Encapsulation, coupling & layering
- **§F** — Readability & explicitness

---

#### Track 4 — Tests

**Focus:** Are tests present, meaningful, and well-structured?

**Assigned sections from the guidelines:**
- **§G** — Tests

---

#### Track 5 — Production Readiness

**Focus:** Will this code be observable and performant in production?

**Assigned sections from the guidelines:**
- **§H** — Observability
- **§J** — Performance & scalability

---

### Step 3 — Aggregate results

After all tracks complete:

1. **Collect** all findings into a single list.
2. **Deduplicate** — if two tracks flag the same line for overlapping reasons, keep the more specific finding.
3. **Sort** — Blockers first, then Suggestions.
4. **Present** a unified review with track attribution for each finding.

### Output format

Present each finding as:

```
[Track N] SEVERITY | file:line | explanation
```

Group by file, then sort by severity within each file.
