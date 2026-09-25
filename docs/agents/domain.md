# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root
- **`CONTEXT-MAP.md`** at the repo root if it exists; it points at one `CONTEXT.md` per context
- **`docs/adr/`** for ADRs touching the area being changed

If these files do not exist, proceed silently.
The `/domain-modeling` skill creates them lazily when terms or decisions are resolved.

## File structure

This repository uses the single-context layout:

```text
/
├── CONTEXT.md
├── docs/adr/
└── src/
```

## Use the glossary's vocabulary

Use terms as defined in `CONTEXT.md` in tickets, proposals, hypotheses, and tests.
If a needed concept is absent, reconsider whether the term belongs or note the gap for `/domain-modeling`.

## Flag ADR conflicts

Surface any conflict with an existing ADR explicitly rather than silently overriding it.
