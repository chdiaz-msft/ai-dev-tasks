# Rubber Duck Review: PRD — PR Split Copilot Agent Skill

## Overall Assessment

The PRD is **directionally strong** — the phased workflow, user stories, and non-goals are well-structured. However, several requirements are too broad or ambiguous for a reliable v1 implementation. No fatal conceptual issues, but product decisions are needed before implementation.

---

## Blocking Issues (Must Address Before Implementation)

### Completeness Gaps

1. **Original PR disposition after child PRs are created** — The PRD defers closing the original PR to a second confirmation gate, but doesn't address what happens in between. The original PR should be converted to draft or labeled to prevent accidental merge while child PRs are active.

2. **Commit metadata preservation** — File-based splitting creates new commits, losing original authorship, co-authors, trailers (`Signed-off-by`, issue refs), and GPG signatures. The PRD needs to define what metadata must be preserved.

3. **Partially overlapping files in commit-based strategy** — A "clean" commit may still include files belonging to different logical groups. Cherry-picking can duplicate, omit, or conflict. Validation that selected commit ranges produce the intended file set per group is missing.

4. **No state persistence model** — Req 26 says "track completed steps," but a Copilot skill can be interrupted. The PRD doesn't define whether state lives in-memory, in branch naming, local files, or GitHub comments.

### Feasibility Concerns

5. **"Independently reviewable" vs. stacked PRs** — Later PRs target prior split branches, so they are NOT independently reviewable without the prior stack. Rephrase to "focused and reviewable in stack order" or define independence as "diff against its immediate parent is coherent."

6. **Semantic dependency analysis is too ambitious for language-agnostic v1** — Requirements like "generated clients before consumers" and "schema before model code" require domain-specific inference. Downgrade to heuristic/conservative for v1 and lean on user plan review.

7. **Per-group build/test validation is under-specified** — Language-agnostic validation can't reliably discover commands. Define a discovery order: user-provided → package scripts → CI config → skip with warning.

### Clarity & Ambiguity

8. **No spec for user editing of the split plan** — Confirmation is all-or-nothing. Can users modify groupings, names, order, or dependencies? v1 should define: confirm/reject only, or allow revision requests.

9. **"Known build/test commands" is ambiguous** — Define a discovery order or explicitly make this user-provided.

10. **"PR head branch is checked out locally" is ambiguous** — Should the skill auto-fetch and checkout, or fail if not already checked out?

### Consistency Issues

11. **Language-agnostic requirement contradicts broad semantic dependency analysis** — The PRD says no language-specific tooling, but expects dependency analysis that often requires language semantics. Downgrade to heuristic warnings.

---

## Non-Blocking Issues (Address or Acknowledge)

- **Generated files, lockfiles, vendored files, submodules** — Not addressed; should keep with the dependency/config change that produced them.
- **Binary/LFS files** — Per-file diff analysis may fail; need conservative grouping.
- **Renames in execution** — `git checkout <original-branch> -- <file>` can mishandle rename/delete semantics.
- **Original PR comments/reviews** — Existing review context is lost; at minimum link back.
- **Branch name sanitization** — No slugification, max length, or collision behavior defined.
- **Original PR success metric vs. optional closure** — "Zero manual cleanup" conflicts with closure being optional.

---

## Highest Risks

| Risk | Mitigation |
|------|-----------|
| **Incorrect split → broken stacked PRs** | Conservative grouping, explicit warnings, validate after each branch |
| **Git state mutation + partial failure** | Precomputed plan, deterministic naming, step log, rollback commands |
| **Stacked PR lifecycle complexity** | Document merge/rebase expectations in every child PR body |
| **False confidence from AI grouping** | Show concrete file lists, dependency rationale, risk flags; allow revision |
| **Branch/PR collisions on rerun** | Detect existing branches, offer cleanup or resume |

---

## Missing Open Questions (Add to PRD)

1. Should users be able to **edit the proposed plan** before execution?
2. What happens to the **original PR** between child PR creation and closure? (Draft? Label?)
3. How should **reruns/resume after partial failure** work?
4. How should **commit metadata** (authorship, co-authors, trailers) be preserved?
5. How are **deletes, renames, mode changes, submodules, binary, LFS** files handled in execution?
6. Should the skill support **user-provided grouping hints**?
7. Should **assignees/projects** (not just labels/milestone) be copied?
8. How does **branch protection / CI** behave on stacked PR branches targeting non-default branches?

---

## Recommendations for Existing Open Questions

| Open Question | Recommendation |
|---------------|----------------|
| Build/test validation | Default: ask user for commands or skip with warning. Make configurable. |
| PR description preservation | Each child PR links back to original; relevant section excerpt in body. |
| Reviewer assignment | Copy reviewers from original. Let CODEOWNERS supplement. |
| Maximum group count | Soft cap at 5 with warning + extra confirmation above threshold. |
| Merge strategy | Document merge-commit (not squash) as recommended for stacked PRs. |
