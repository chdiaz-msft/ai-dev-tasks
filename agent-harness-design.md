# Agentic Engineering Harness

## Goal

Develop a **software engineering manager agent** that we can use to automate long software engineering tasks.

---

## References & Inspiration

- [What is Agentic AI Engineering (Meta Staff Engineer Explains)](https://www.youtube.com/watch?v=example)
- [Harness Engineering: Leveraging Codex in an Agent-First World | OpenAI](https://openai.com/index/harness-engineering/)
  - Key takeaway: domain knowledge should exist in the codebase
- [I had Claude read every harness engineering guide and build me one (r/ClaudeAI)](https://www.reddit.com/r/ClaudeAI/)
- Adversarial Dev Technique

---

## Input

_TBD_

## Output

_TBD_

---

## Phases

1. **Domain Exploration & Knowledge Dump** — Gather relevant context information from the user and the codebase.
2. **PRD Creation**
3. **PRD Validation**
4. **PRD Open Questions** — Surface and resolve ambiguities before proceeding.
5. **Task Creation** — Focus on creating tasks only within the realm of capabilities the agent actually has.
6. **Task Review**
7. **Task Execution** — With explicit callout if a task couldn't be completed by an agent, and why.
8. **UX Validation**
9. **UX Design**
10. **Code Cleanup** — Removing competing patterns and consolidating conventions.

---

## Context

_TBD_

---

## Agents

| Agent          | Role |
|----------------|------|
| **Orchestrator** | Directs other agents; does no low-level work itself. |
| **Coder**        | Implements tasks, writes code. |
| **Reviewer**     | Reviews PRs, validates quality. |
| **Researcher**   | Gathers context, explores domain, answers open questions. |

---

## Skills

- Create PRD
- Revise PRD
- Review PR (with swarm of various reviewers)
- Make PR revision
- Resolve PR comments (always with a comment)
- Test UX
- Ralph execute
- Log GitHub issue
- Add ADR
- Search internal team documentation
- _Maybe: a skill to write a `Claude.md` file or spin up a new agent that'd be helpful for the project (e.g., Financial Analyst agent)_

---

## Folder Structure

```
PRDs/
├── Active/
└── Completed/

Tasks/
├── Active/
└── Completed/

ADRs/
```

---

## Principles

1. **Spec-Driven Development** — Always work from a spec.
2. **Context is MOST Important** — Rich context leads to better outcomes.
3. **Verify Work Often** — The agent should frequently verify its own work.
4. **Deep Discovery First** — The agent should do _lots_ of user interviewing and discovery prior to starting any work. It should attempt to learn things such as the timeline, intended fidelity, scope of the final solution, etc.
5. **DAG-Based Work Breakdown** — Model the work breakdown as a DAG and strongly enforce dependencies.
6. **One Orchestrator, No Low-Level Work** — There is one main orchestrator agent which only directs other agents; it never does low-level work itself.
7. **Reuse Existing Tools** — Reuse existing and well-maintained tools as much as possible. Don't reinvent tools.
8. **Immutable History** — Once accepted, an ADR is not edited. If the decision changes, a new "superseding" ADR is created.
9. **Parallelization** _(not high priority)_ — Leverage parallelization whenever possible.
10. **Escalate Open Questions** — Escalate open questions to the user if the agent is unable to resolve them itself.
11. **Right Model for the Job** — Use various models for certain tasks (e.g., PR review, PRD writing, etc.).
12. **Prioritize Ruthlessly** — What is nice to have and can be pushed off?

---

## Useful Plugins

_TBD_

---

## Open Questions

- How often should the agent commit its changes? Define a clear policy for when commits happen.
- How do we communicate with / get notifications from the agent?
- How do we identify blocking points (e.g., something that needs to be validated by hand)?
- Which actions can **NOT** be delegated to the agent and need to be human-run?
- Which credentials are needed for the agent to do meaningful work (e.g., Azure credentials)?
- How can the agent easily search up key design decisions?
- Which documents should the PRD reference?
- How can we have the agent run indefinitely without its context getting too large?
- Can we use Gstack's tool for UX validation?
- Can we detect when an agent is stuck spinning in circles?
- How do we extract key knowledge from meetings to input into the codebase?
- Which code linter practices do we want to enforce?

---

## Validation

- ADB for mobile

---

## Eval

- How are we going to evaluate this harness?
