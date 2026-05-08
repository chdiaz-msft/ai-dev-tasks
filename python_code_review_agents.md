# Python Code Review — Parallel Agent Dispatch Guide

> **Purpose:** Split a Python code review across multiple independent subagents, each focused on a distinct concern. This file defines the review tracks, what each agent owns, and how to combine results.
>
> **Source of truth:** All checklists live in [`python_code_review_guidelines.md`](python_code_review_guidelines.md). Each track below references the sections an agent must review — read the checklist items from the guidelines doc, not this file.

## How to use

Dispatch one subagent per track below. Each agent receives:
1. The diff / file list under review.
2. The full [`python_code_review_guidelines.md`](python_code_review_guidelines.md).
3. Its track definition from this file (focus area + assigned sections).
4. The instruction: *"Review ONLY your assigned sections. Output a structured list of findings with file, line, severity (Blocker / Suggestion), and explanation."*

Agents run in parallel with no dependencies between them. A final aggregator collects all findings, deduplicates, and presents a unified review.

---

## Track 1 — Correctness, Contracts & Safety

**Focus:** Does the code do what it claims, fail safely, and avoid security holes?

**Assigned sections:**
- **§A** — Correctness & contracts
- **§C** — Defaults & fail-fast
- **§D** — Error handling & resilience
- **§I** — Security & secrets

---

## Track 2 — Type System & Modeling

**Focus:** Is the type system pulling its weight? Are domain concepts modeled precisely?

**Assigned sections:**
- **§B** — Type safety & modeling (all subsections: `Any` avoidance, rich models, constrained generics, type system lapses, nullability, immutability, primitive obsession)

---

## Track 3 — Architecture & Design

**Focus:** Is the code well-structured, properly layered, and easy to understand?

**Assigned sections:**
- **§E** — Encapsulation, coupling & layering
- **§F** — Readability & explicitness

---

## Track 4 — Tests

**Focus:** Are tests present, meaningful, and well-structured?

**Assigned sections:**
- **§G** — Tests

---

## Track 5 — Production Readiness

**Focus:** Will this code be observable and performant in production?

**Assigned sections:**
- **§H** — Observability
- **§J** — Performance & scalability

---

## Pre-review gate (run before dispatching agents)

Before dispatching review agents, verify the author has completed **§0 — Pre-review gates** from the guidelines. If any gate fails, return the PR to the author before spending agent review cycles.

---

## Aggregation

After all tracks complete, the aggregator should:
1. Collect all findings into a single list.
2. Deduplicate (two tracks may flag the same line for overlapping reasons — keep the more specific finding).
3. Sort: Blockers first, then Suggestions.
4. Present as a unified review with track attribution for each finding.
