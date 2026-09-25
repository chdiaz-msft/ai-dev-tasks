# Issue tracker: Local Markdown

Issues and specs for this repo live as Markdown files in `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`, never a single combined tickets file
- Triage state is recorded as a `Status:` line near the top of each issue file
- Comments and conversation history append to the bottom of the file under a `## Comments` heading

## When a skill says "publish to the issue tracker"

Create a new file under `.scratch/<feature-slug>/`, creating the directory if needed.

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path.
The user will normally pass the path or ticket number directly.

## Wayfinding operations

Used by `/wayfinder`.
The map is a file with one child file per ticket.

- **Map**: `.scratch/<effort>/map.md` containing Notes, Decisions-so-far, and Fog
- **Child ticket**: `.scratch/<effort>/issues/NN-<slug>.md`, numbered from `01`, with a `Type:` line for `research`, `prototype`, `grilling`, or `task`, plus a `Status:` line for `claimed` or `resolved`
- **Blocking**: a `Blocked by: NN, NN` line near the top; a ticket is unblocked when every listed file is `resolved`
- **Frontier**: scan `.scratch/<effort>/issues/` for open, unblocked, unclaimed files; first by number wins
- **Claim**: set `Status: claimed` and save before any work
- **Resolve**: append the answer under `## Answer`, set `Status: resolved`, then append a context pointer to the map's Decisions-so-far
