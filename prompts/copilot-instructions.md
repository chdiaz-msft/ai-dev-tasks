# Custom Review Doctrine for [Your Repo Name]

This file allows you to customize the PR Flywheel's behavior for your repository. When present, this doctrine supplements or overrides the default review doctrine defined in the PR Flywheel template.

## How to Use This File

1. **Copy this template** to the root of your repository as `prompts/copilot-instructions.md`
2. **Customize the sections below** to match your project's needs
3. **Commit and push** — the Flywheel workflow will automatically use your custom doctrine

---

## Project Context

<!-- Provide context about your project to help reviewers understand domain-specific concerns -->

**Project Type:** [e.g., Web API, CLI tool, Data pipeline, Frontend SPA, Mobile backend]

**Primary Language(s):** [e.g., TypeScript, Python, Go, Rust]

**Critical Paths:** [List paths that require extra scrutiny, e.g., `src/billing/`, `lib/auth/`, `db/migrations/`]

**Testing Strategy:** [e.g., Unit tests with Jest, Integration tests with pytest, E2E with Playwright]

**Deployment Model:** [e.g., Serverless on AWS Lambda, Kubernetes cluster, Static site on Vercel]

---

## Custom Severity Mappings

Override or refine the default severity scale for your domain-specific issues.

### Project-Specific Critical Issues

In addition to the default critical issues, the following are considered **critical** for this project:

- **Database migration errors**: Any issue in `db/migrations/**` that could corrupt production data
- **Billing logic errors**: Incorrect calculations in `src/billing/**` that could overcharge or undercharge users
- **[Add your domain-specific critical concerns here]**

### Downgrade Severity

Some issues that are normally high-severity can be downgraded for this project:

- **Missing input validation in internal tools**: Internal admin tools (paths under `src/admin/**`) may have relaxed validation since they're not public-facing → downgrade from **high** to **medium**
- **[Add your exceptions here]**

---

## Path Exclusions

Exclude certain paths from automated review or fix attempts. The Flywheel will skip these paths entirely.

```yaml
# Paths to exclude from all reviews
exclude_from_review:
  - "vendor/**"              # Third-party dependencies
  - "generated/**"           # Auto-generated code
  - "docs/**"                # Documentation (markdown, etc.)
  - "scripts/legacy/**"      # Legacy scripts not under active maintenance
  - "*.min.js"               # Minified files
  - "*.lock"                 # Lock files (package-lock.json, poetry.lock, etc.)

# Paths to review but never auto-fix (require human approval)
review_only_no_fix:
  - "src/core/engine.ts"     # Core business logic
  - "db/migrations/**"       # Database migrations
  - "config/production.yml"  # Production configuration
```

---

## Custom Review Focus

Define project-specific areas of concern that reviewers should prioritize.

### High-Priority Concerns

1. **Performance in hot paths**: Any changes to `src/api/handlers/**` must be scrutinized for performance regressions (e.g., N+1 queries, inefficient loops).

2. **Backward compatibility**: Changes to public API endpoints (`src/api/v1/**`) must maintain backward compatibility. Flag any breaking changes as **high** severity.

3. **Security in user-facing code**: All code in `src/web/**` must be reviewed for XSS, CSRF, and injection vulnerabilities.

4. **Error handling**: All async functions in `src/services/**` must have proper error handling and logging.

5. **[Add your project-specific concerns here]**

### Low-Priority or Ignored Concerns

1. **Test coverage**: Don't flag missing test coverage as an issue — we track it separately via CI.

2. **Code style**: Ignore all style issues — we use `eslint` and `prettier` for that.

3. **Performance micro-optimizations**: Don't flag minor performance improvements unless they're in hot paths (see above).

---

## Domain-Specific Terminology

Define project-specific terms to help reviewers understand your codebase.

| Term | Definition |
|------|------------|
| **Widget** | The primary domain entity representing a user's configuration object |
| **Tenant** | Multi-tenant isolation boundary; each tenant has isolated data |
| **Job** | Background task executed by the worker pool |
| **Pipeline** | A sequence of processing stages for data transformation |
| **[Your term]** | [Your definition] |

---

## Severity Floor Override

Override the default severity floor for automated fixes.

```yaml
# Default severity floor is "high" — only critical and high-severity issues are auto-fixed
# You can raise or lower this threshold:

severity_floor: "high"  # Options: critical, high, medium, low, info

# Examples:
# - "critical": Only auto-fix critical issues (most conservative)
# - "high": Auto-fix critical and high issues (default, recommended)
# - "medium": Auto-fix critical, high, and medium issues (more aggressive)
```

**Recommendation:** Keep the default `"high"` unless you have a strong reason to change it. Auto-fixing medium-severity issues can introduce unnecessary churn.

---

## Testing Requirements

Define what "green tests" means for your project.

```yaml
# Test commands the fix agent should run before considering a fix complete
test_commands:
  - "npm run lint"           # Linting must pass
  - "npm run typecheck"      # Type checking must pass
  - "npm test"               # Unit tests must pass
  - "npm run test:integration"  # Integration tests must pass (if applicable)

# Optional: Define a timeout for test execution (in seconds)
test_timeout: 300  # 5 minutes
```

---

## Commit Message Style

Customize the commit message style for automated fixes.

```yaml
# Conventional Commits configuration
commit_style: "conventional"  # Options: conventional, simple

# Commit message prefix for flywheel commits
commit_prefix: "fix(flywheel):"

# Example output: "fix(flywheel): correct off-by-one error in pagination logic"
```

---

## Reviewer-Specific Overrides

Customize behavior for individual reviewers (correctness, security).

### Correctness Reviewer

```yaml
correctness:
  # Additional focus areas beyond the defaults
  extra_focus:
    - "Ensure all database queries are properly parameterized (no string concatenation)"
    - "Check that all API responses include proper error codes and messages"
    - "Validate that async/await is used correctly (no missing awaits)"

  # Ignore these patterns for correctness review
  ignore_patterns:
    - "src/test/**"  # Test utilities don't need strict correctness
```

### Security Reviewer

```yaml
security:
  # Additional focus areas beyond the defaults
  extra_focus:
    - "All user inputs in `src/web/**` must be sanitized before rendering"
    - "All database queries must use parameterized queries or an ORM"
    - "No logging of sensitive data (PII, tokens, passwords)"

  # Known false positives to ignore
  ignore_patterns:
    - "src/test/fixtures/**"  # Test fixtures may contain mock sensitive data

  # Severity overrides
  severity_overrides:
    - pattern: "hardcoded API key in test file"
      severity: "medium"  # Downgrade from critical if it's a test API key
```

---

## Human Escalation Rules

Define when the Flywheel should escalate to human review instead of auto-fixing.

```yaml
escalate_to_human:
  # Escalate based on file patterns
  - pattern: "src/core/**"
    reason: "Core business logic requires human review"

  # Escalate based on issue type
  - issue_contains: "breaking change"
    reason: "Breaking changes require product team approval"

  # Escalate based on severity + location
  - severity: "critical"
    path: "src/billing/**"
    reason: "Critical billing issues require manual verification"
```

---

## Integration with Existing Tools

Define how the Flywheel should interact with your existing CI/CD and review tools.

### Required Checks

```yaml
# The Flywheel will wait for these checks to pass before proceeding
required_checks:
  - "build"
  - "test"
  - "lint"
  - "typecheck"
  - "security-scan"

# If any required check fails, the Flywheel will pause and wait for a fix
```

### Label Behavior

```yaml
# Labels that trigger specific Flywheel behavior
labels:
  pause: "flywheel:pause"       # Stop the Flywheel loop entirely
  skip_review: "flywheel:skip"  # Skip automated review for this PR
  force_review: "flywheel:force"  # Force a review even if normally skipped
  human_only: "flywheel:human"  # Review but never auto-fix
```

---

## Example Configuration (Complete)

Here's a complete example configuration for a TypeScript API project:

```yaml
# prompts/copilot-instructions.md

project:
  name: "Acme API"
  type: "REST API"
  language: "TypeScript"

severity_floor: "high"

exclude_from_review:
  - "dist/**"
  - "node_modules/**"
  - "coverage/**"
  - "*.min.js"

review_only_no_fix:
  - "src/billing/**"
  - "db/migrations/**"
  - "config/production.ts"

correctness:
  extra_focus:
    - "All database queries must use parameterized queries"
    - "All async functions must have proper error handling"
  ignore_patterns:
    - "src/**/*.test.ts"

security:
  extra_focus:
    - "No logging of PII or tokens"
    - "All user inputs must be validated and sanitized"
  severity_overrides:
    - pattern: "test API key"
      severity: "low"

test_commands:
  - "npm run lint"
  - "npm run typecheck"
  - "npm test"

commit_prefix: "fix(auto):"

required_checks:
  - "build"
  - "test"
  - "typecheck"

escalate_to_human:
  - pattern: "src/billing/**"
    reason: "Billing code requires manual review"
  - severity: "critical"
    reason: "All critical issues require human verification"
```

---

## Notes

- **Start conservative**: Begin with a high severity floor and strict path exclusions. Loosen restrictions as you gain confidence in the system.
- **Iterate based on results**: Review the Flywheel's automated fixes in the first few PRs and adjust this doctrine accordingly.
- **Document exceptions**: If you add a custom rule, document *why* in a comment so future maintainers understand the rationale.
- **Keep it updated**: As your project evolves, revisit this doctrine and update it to reflect new priorities or lessons learned.

---

## Support

For questions or issues with the PR Flywheel:
- See the main template repository: [org/pr-flywheel-template](https://github.com/org/pr-flywheel-template)
- File an issue: [org/pr-flywheel-template/issues](https://github.com/org/pr-flywheel-template/issues)
- Pause the Flywheel: Add the `flywheel:pause` label to any PR
