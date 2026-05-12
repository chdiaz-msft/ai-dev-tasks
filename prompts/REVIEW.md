# Default Review Doctrine

This document defines the default review doctrine for the PR Flywheel system. It establishes the severity scale, general review rules, and output expectations for all reviewers.

## Severity Scale

All reviewers must classify findings using the following severity levels:

### Critical
**Definition:** Issues that pose immediate, severe risk to production systems, data integrity, or security.

**Examples:**
- SQL injection vulnerabilities allowing arbitrary database access
- Remote code execution vulnerabilities
- Authentication bypass allowing unauthorized access
- Data loss or corruption bugs in production code
- Hardcoded production credentials or API keys
- Memory safety violations that can be reliably exploited

**Action:** Must be fixed immediately. Block merge until resolved.

---

### High
**Definition:** Issues that represent significant defects in core functionality, security weaknesses, or serious correctness problems.

**Examples:**
- Logic errors that cause incorrect business-critical calculations
- Unhandled exceptions that crash the application
- Race conditions in concurrent code
- Cross-site scripting (XSS) vulnerabilities
- Broken error handling that silently fails
- API contract violations that break existing clients
- Off-by-one errors in critical algorithms

**Action:** Should be fixed before merge. May proceed with explicit approval if mitigation plan exists.

---

### Medium
**Definition:** Issues that impact code quality, maintainability, or represent potential future problems.

**Examples:**
- Missing input validation for non-critical paths
- Inefficient algorithms that don't impact current scale
- Missing error messages or poor error handling
- Code that violates established patterns without good reason
- Type safety issues in non-critical code paths
- Minor security hardening opportunities (e.g., missing HTTPS enforcement in development)

**Action:** Should be addressed, but may be deferred to follow-up PR if technical debt is tracked.

---

### Low
**Definition:** Minor issues, style inconsistencies, or suggestions for improvement.

**Examples:**
- Inconsistent naming conventions
- Missing or incomplete code comments
- Unused imports or variables
- Minor code duplication
- Overly complex expressions that could be simplified
- Missing test coverage for edge cases

**Action:** Optional to fix. Never auto-fix low-severity issues. Human discretion required.

---

### Info
**Definition:** Observations, questions, or suggestions that don't represent defects.

**Examples:**
- Alternative implementation approaches
- Performance optimization opportunities
- Suggestions for future refactoring
- Questions about design decisions
- Educational comments or links to documentation

**Action:** No action required. Informational only.

---

## General Review Rules

1. **Focus on correctness and security first.** Logic bugs and security vulnerabilities take precedence over style, performance, or maintainability concerns.

2. **Never auto-fix low-severity issues.** Low and info-level findings are for human awareness only. The flywheel should not attempt automated fixes for these.

3. **Be specific and actionable.** Every finding must identify:
   - The exact file and line number
   - What is wrong
   - Why it matters (impact)
   - A concrete suggested fix

4. **Assume context awareness.** Reviewers have access to the full diff, prior issues from previous rounds, and the PR description. Use this context to avoid duplicate findings.

5. **Prioritize high-signal findings.** Avoid nitpicking or reporting issues that are clearly out of scope for the current PR.

6. **Respect severity boundaries.** Do not inflate severity to force action. Trust the system to handle appropriately-classified findings.

7. **Ignore style and formatting.** Do not report issues that should be handled by automated linters or formatters (e.g., whitespace, indentation, line length).

---

## Output Expectations

All reviewers must return structured findings in JSON format:

```json
[
  {
    "issue_id": "reviewer-abc12345",
    "reviewer": "correctness",
    "severity": "high",
    "issue": "Unhandled null pointer dereference in user lookup",
    "suggested_fix": "Add null check: if (user === null) { return null; } before accessing user.name",
    "file": "src/services/user.ts",
    "line": 42
  }
]
```

### Required fields:
- `issue_id` — Deterministic identifier (format: `{reviewer}-{hash[:8]}`)
- `reviewer` — Name of the reviewer (e.g., "correctness", "security")
- `severity` — One of: `critical`, `high`, `medium`, `low`, `info`
- `issue` — Clear description of the problem
- `suggested_fix` — Concrete, actionable fix guidance
- `file` — Relative path from repository root
- `line` — Line number (integer) or `null` if file-level issue

### Constraints:
- Each finding must be independent and self-contained
- Issue descriptions should be 1-3 sentences
- Suggested fixes should be specific enough for an AI agent to implement
- Do not duplicate findings across reviewers (coordinate via issue_id)

---

## Customization

Repositories may override this default doctrine by providing a custom doctrine file at `prompts/copilot-instructions.md` or similar. Custom doctrines can:
- Adjust severity thresholds for specific types of issues
- Add domain-specific review rules
- Exclude certain paths or patterns from review
- Define project-specific terminology or standards

When a custom doctrine is provided, it takes precedence over this default.
