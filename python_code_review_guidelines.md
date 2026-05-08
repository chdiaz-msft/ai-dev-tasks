# Python Code Review Guidelines (Junior-Friendly)

> **Goal:** Build robust, maintainable systems by making bugs structurally difficult to write: strong modeling, fail-fast boundaries, minimal coupling, and pragmatic simplicity.

## How to use this checklist
- **Automate the mechanical checks** (format/lint/typecheck/tests) so human review focuses on design, correctness, and risk.
- Treat **“Blockers”** as must-fix before merge; treat **“Suggestions”** as improvements when they materially raise code health.

---

## 0) Pre-review gates (author responsibilities)
- [ ] Formatter + linter are clean (`ruff`; avoid style debate in PR).
- [ ] Type checker is clean (`pyright` in **strict** mode—every function needs parameter types and return types).
- [ ] Tests pass and cover changed behavior (`uv run pytest`).
- [ ] All commands run through `uv` (e.g., `uv run pytest`, `uv run python`).
- [ ] PR description answers: **why**, **what**, **how to validate**.

---

## A) Correctness & contracts (**Blockers**)
- [ ] Change matches intended behavior (ticket/requirements).
- [ ] Edge cases handled explicitly (empty inputs, missing files, bad input, timeouts).
- [ ] No silent behavior changes to public APIs (signatures, return types, error behavior).
- [ ] Exceptions are either handled at boundaries or documented/expected by callers.

---

## B) Type safety & modeling (**High leverage**)
### 1) Don’t “type-wash” with `Any`
- [ ] No `Any` when the type is knowable.
- [ ] If `Any` is unavoidable (interop/untyped libs), keep the scope tiny and convert to typed data immediately after validation/parsing.
- [ ] Prefer **`Protocol`**/interfaces for abstraction instead of `Any`.

### 2) Prefer rich models over bags-of-stuff
- [ ] Prefer strong types over `dict` for known schemas.
  - Known schema → `FrozenBaseModel` (preferred), `dataclass`, or `TypedDict`.
  - `dict` only for truly dynamic content.
- [ ] Use discriminated unions for variants (each subtype gets a real type).
  - Avoid one “mega” model with many optional fields.

### 3) Constrain generics
- [ ] Generic type parameters are bounded when the implementation assumes a meaningful capability.

### 4) Concrete “type system lapses” to look for
- [ ] Avoid “stringly-typed” state/flags (use `Enum` or `Literal` unions).
- [ ] Avoid ambiguous return types (e.g., `dict | None`) when a dedicated result type improves clarity.

### 5) Nullability — pick the right pattern
- [ ] Understand the three patterns and use the correct one:
  - `name: str` — required, non-nullable (caller must provide a value).
  - `middle_name: str | None` — required, nullable (caller must explicitly pass a value or `None`; useful for “update” models to distinguish “don't change” from “set to null”).
  - `nickname: str | None = None` — optional, nullable (can be omitted, defaults to `None`).
- [ ] Never use empty string, zero, or `False` to represent “no value”—use `None`.

### 6) Immutability
- [ ] Models inherit from `FrozenBaseModel` (instances are frozen after creation).
- [ ] Functions do not mutate their arguments; return new objects instead.

### 7) Avoid **primitive obsession** (what it means)
> Primitive obsession is representing domain concepts as raw primitives (str/int/bool) so rules/validation spread everywhere.

Review checks:
- [ ] Domain concepts have domain types (value objects): e.g., `Email`, `Money`, `Port`, `AccountId`.
- [ ] Validation lives **once** inside the type (or at the boundary), not duplicated throughout the codebase.
- [ ] Avoid magic constants encoding meaning (prefer enums / explicit types).

---

## C) Defaults & fail-fast (**Blockers**)
- [ ] Required values don’t have defaults that hide missing input.
- [ ] Validate at boundaries; fail fast and loudly (no silent fallbacks).
- [ ] No mutable default arguments (`[]`, `{}`, `set()`): use `None` + allocate inside.
- [ ] Optional fallbacks exist only with a documented reason (ideally with metrics/logging so you can see their frequency).
- [ ] Prefer explicit **no-op implementations** over scattered `Optional` + null checks everywhere.
- [ ] Environment variables use `pydantic-settings` (`Settings` class), not `os.environ` (centralized, typed, validated at startup).

---

## D) Error handling & resilience (**Blockers**)
- [ ] Catch **specific** exceptions; avoid broad `except Exception` without justification.
- [ ] `try` blocks are minimal (wrap only the lines that can fail).
- [ ] Don't wrap everything in `try/except`—only catch exceptions you can actually *handle* (middleware handles unhandled exceptions automatically).
- [ ] No catch-log-rethrow patterns that double-log or obscure ownership.
- [ ] Resource handling uses context managers (`with open(...)`, DB sessions, locks).
- [ ] Errors are logged with useful context (safe-to-log identifiers) and preserve exception chains when wrapping.
- [ ] Domain-specific exception hierarchies use a common base (e.g., `InsufficientFundsError(PaymentError)`) so callers can catch broadly or narrowly.
- [ ] In FastAPI, use `FdeCustomException` with specific error codes, not `HTTPException`.

---

## E) Encapsulation, coupling & layering (**High leverage**)
- [ ] Dependencies are injected (parameters/constructors) rather than hidden globals/singletons.
- [ ] Responsibilities are in the right layer:
  - Domain logic is isolated from I/O.
  - Adapters/wrappers handle external systems.
  - Orchestration is thin.
- [ ] Types/models are in their own module, separate from logic (prevents circular imports for downstream consumers).
- [ ] API models are separate from domain models (don't expose internal fields like `password_hash` to API clients).
- [ ] Avoid brittle coupling to naming/path conventions; prefer explicit registries/manifests.

**Review smell (easy heuristic):** if tests require heavy patching of module globals, dependencies likely aren’t explicit enough.

---

## F) Readability & explicitness (**Blockers where it affects correctness**)
- [ ] Names/docstrings/comments reflect *current* behavior (updated when behavior changes).
- [ ] Public exports are intentional (exports are contracts).
- [ ] Dead code is removed (unreachable branches, unused helpers, speculative features).
- [ ] Functions/classes do one thing; avoid deep nesting and giant functions.

---

## G) Tests (**Blockers**)
- [ ] Tests cover changed behavior and critical branches.
- [ ] New logic has tests that would have failed before the change (regression-proof).
- [ ] Tests are deterministic (control time/network/randomness).
- [ ] Tests follow **Arrange / Act / Assert** structure.
- [ ] Test names are descriptive—name explains what it verifies (e.g., `test_parse_date_returns_tuple_for_valid_iso_format`, not `test_parse_1`).
- [ ] Related scenarios use `@pytest.mark.parametrize` instead of copy-pasted tests.
- [ ] Tests verify **behavior** (observable outcomes), not implementation details (avoid brittle mock assertions like `mock._internal_method.assert_called_once_with(...)`).
- [ ] Prefer shared **fakes** (in `tests/fakes/`) over scattered `MagicMock` setups when the same dependency is mocked across many tests.

---

## H) Observability (**Production readiness**)
- [ ] Never use `print()` in libraries or applications—use `logging.getLogger(__name__)`.
- [ ] Logging is structured/consistent and primarily at boundaries (request→response, job start/end).
- [ ] Logs/metrics enable diagnosing failures, latency, and dependency issues.
- [ ] Avoid “trace just in case”; log with intent.

---

## I) Security & secrets (**Blockers**)
- [ ] No secrets in source/config/logs; retrieve via Key Vault or env-based mechanisms.
- [ ] External calls use timeouts/retries and validate endpoints/inputs.
- [ ] Untrusted input is validated/sanitized.

---

## J) Performance & scalability (**Check when relevant**)
- [ ] No accidental O(n²) loops; avoid repeated expensive work inside loops.
- [ ] Prefer library/vectorized operations when it improves clarity/perf.
- [ ] Avoid expensive regex/string work when unnecessary.
- [ ] (PySpark) Minimize `.collect()` and avoid actions inside loops.

---

## Philosophy (why this works)
The type system and explicit design should do the heavy lifting, so bugs become structurally difficult rather than caught at runtime. Strong modeling, fail-fast semantics, minimal coupling, and pragmatic complexity—**ruthlessly remove anything not used today**.

---

## References
- PEP 8 — Style Guide for Python Code: https://peps.python.org/pep-0008/
- PEP 484 — Type Hints: https://peps.python.org/pep-0484/
- Google Python Style Guide: https://google.github.io/styleguide/pyguide.md
- Primitive Obsession (code smell): https://refactoring.guru/smells/primitive-obsession
