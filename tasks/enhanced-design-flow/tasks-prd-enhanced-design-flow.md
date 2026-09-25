## Relevant Files

- `research-brief.prompt.md` - Optional pre-PRD prompt for domain/landscape research
- `analyze-problem.prompt.md` - Optional pre-PRD prompt for structured problem decomposition (Karpathy + First Principles)
- `design-architecture.prompt.md` - Mandatory post-PRD prompt for technical architecture design
- `review-and-refine.prompt.md` - Mandatory post-architecture prompt for critical review and refinement
- `optimize-prompt.prompt.md` - Standalone utility prompt for improving any prompt file
- `create-prd.prompt.md` - Existing PRD prompt (to be modified)
- `generate-tasks.prompt.md` - Existing task generation prompt (to be modified)
- `task-helpers/process-task-list.prompt.md` - Existing task execution system prompt (to be modified)
- `complete-feature.prompt.md` - Existing feature archival prompt (to be modified)

### Notes

- These are prompt/markdown authoring tasks, not code — TDD does not apply.
- All new prompt files go in the repo root alongside existing `.prompt.md` files.
- All prompts must use the same YAML frontmatter format as existing prompts (`description`, `globs`, `alwaysApply: false`).
- Artifact naming convention: `[type]-[feature-name].md` inside `tasks/[feature-name]/`.
- Reference the plan at `.claude/plans/wild-tumbling-hare.md` for full design details.

## Source Prompts

The following reference prompts inform the content of each new prompt file. Each task maps to one or more of these patterns.

### Prompt 1: Karpathy Decomposition (→ analyze-problem.prompt.md)

```
Approach this like a systems thinker.

1. Define the core problem clearly
2. Identify assumptions
3. List constraints and unknowns
4. Break into sub-problems
5. Propose 3 different approaches
6. Compare tradeoffs
7. Choose best approach
8. Give step-by-step execution
9. Highlight failure points
10. Suggest improvements after v1

Problem: [paste]
```

### Prompt 2: First Principles Builder (→ analyze-problem.prompt.md)

```
Explain this from first principles.

Start with the most fundamental concepts.
Build upward layer by layer.
Avoid analogies initially.
Define terminology clearly.
Show how each layer connects.
Then provide mental model.
Then show real-world application.
Then give common misconceptions.
Then summarize in 5 bullet points.

Topic: [insert]
```

### Prompt 3: Research Brief Generator (→ research-brief.prompt.md)

```
Create a high-quality research brief on this topic.

Include:
• overview of space
• key players
• current approaches
• what's working
• what's failing
• gaps in the market
• emerging trends
• contrarian insights
• opportunities to build
• actionable takeaways

Topic: [insert]
```

### Prompt 4: Build Architecture (→ design-architecture.prompt.md)

```
I want to build this. Design the implementation.

Give:
• simplest possible version
• architecture diagram (text)
• components
• data flow
• tech stack
• step-by-step build order
• edge cases
• scaling strategy
• possible bottlenecks
• v2 improvements

Idea: [insert]
```

### Prompt 5: Meta Prompt Optimizer (→ optimize-prompt.prompt.md)

```
Rewrite this prompt to maximize output quality.

Improve:
• clarity
• structure
• constraints
• output format
• reasoning depth
• specificity

Return:

1. optimized prompt
2. why it's better
3. when to use it

Prompt: [paste]
```

### Prompt 6: Expert Mode Switch (→ process-task-list.prompt.md)

```
Answer as a senior engineer explaining to another engineer.

Avoid beginner explanations.
Be concise but technical.
Focus on implementation.
Mention tradeoffs.
Include pitfalls.
Include best practices.

Question: [insert]
```

### Prompt 7: Thinking Partner (→ review-and-refine.prompt.md)

```
Act as a critical thinking partner.

Do not agree blindly.
Challenge assumptions.
Point out weak logic.
Suggest alternatives.
Identify risks.
Improve the idea.
Propose better direction.

Idea: [paste]
```

## Tasks

- [ ] 1.0 Create new optional pre-work prompt files
  - [ ] 1.1 Create `research-brief.prompt.md` based on **Prompt 3 (Research Brief Generator)**. Adapt the prompt into a `.prompt.md` file with YAML frontmatter. The structured brief must include: overview of space, key players/approaches, current approaches, what's working, what's failing, gaps, emerging trends, contrarian insights, opportunities to build, actionable takeaways. Additionally, instruct the agent to search the actual codebase for existing patterns and prior art (not just generic knowledge). Output to `tasks/[feature]/research-[feature].md`. Create feature directory if needed. Do NOT proceed to PRD — just produce the brief.
  - [ ] 1.2 Create `analyze-problem.prompt.md` combining **Prompt 1 (Karpathy Decomposition)** + **Prompt 2 (First Principles Builder)**. Weave First Principles into step 4 of the Karpathy flow. Full 10-step process: (1) define the core problem clearly, (2) identify assumptions, (3) list constraints and unknowns, (4) first-principles analysis — start with fundamentals, build upward layer by layer, define terminology clearly, show how each layer connects, provide mental model, note common misconceptions, (5) break into sub-problems, (6) propose exactly 3 different approaches, (7) compare tradeoffs in a structured table (columns: Approach, Pros, Cons, Complexity, Risk), (8) choose best approach with justification, (9) highlight failure points and mitigations, (10) suggest v1 scope vs improvements to defer to v2. Output to `tasks/[feature]/analysis-[feature].md`. Create feature directory if needed.

- [ ] 2.0 Create mandatory design phase prompt files
  - [ ] 2.1 Create `design-architecture.prompt.md` based on **Prompt 4 (Build Architecture)**. Adapt into a `.prompt.md` file. Input: user references the PRD file. Agent also reads any existing analysis/research docs in the same directory. Process must cover all items from Prompt 4: simplest possible version, architecture diagram (text-based in markdown), components (each with name, responsibility, public interface, dependencies), data flow, tech stack with justification, step-by-step build order (this directly feeds task generation), edge cases, scaling strategy / possible bottlenecks, known technical risks, v2 improvements. Output to `tasks/[feature]/architecture-[feature].md`. Must explicitly state: do NOT generate tasks — only produce the architecture document.
  - [ ] 2.2 Create `review-and-refine.prompt.md` based on **Prompt 7 (Thinking Partner)**. Expand the thinking partner pattern into a two-phase structured process. **Phase A — Critical Review:** embody the thinking partner role — do not agree blindly, challenge assumptions, point out weak logic, suggest alternatives, identify risks, propose better direction. Specifically: read PRD + architecture, check requirement coverage gaps, identify weak points (under-specified interfaces, missing error handling, coupling concerns, security issues), challenge assumptions explicitly ("You assumed X — what if Y instead?"), rank risks by severity (High/Medium/Low), suggest alternatives for weakest decisions, flag irreversible decisions that will be painful to change later. **Phase B — Refinement:** for each High/Medium severity issue from Phase A, propose a concrete fix. Produce: Refined Architecture section incorporating all fixes, Deferred Concerns section for Low-severity items and v2 improvements, Key Decisions Log recording what was decided and why. Output to `tasks/[feature]/design-review-[feature].md`. User can re-invoke for another review cycle if not satisfied.

- [ ] 3.0 Create standalone utility prompt file
  - [ ] 3.1 Create `optimize-prompt.prompt.md` based on **Prompt 5 (Meta Prompt Optimizer)**. Adapt into a `.prompt.md` file. Input: user references any `.prompt.md` file. Analyze for all dimensions from Prompt 5: clarity, structure, constraints, output format, reasoning depth, specificity. Also check for: ambiguity, redundancy, missing failure mode handling, weak role definitions. Return: (1) the optimized/rewritten prompt, (2) why each change makes it better, (3) when to use the prompt. Do NOT auto-save changes — print the optimized prompt to the conversation for user review and manual application.

- [ ] 4.0 Modify existing prompt files to integrate with the new design flow
  - [ ] 4.1 Modify `create-prd.prompt.md`: add "Optional Input Context" section instructing agent to check for and incorporate `research-[feature].md` and `analysis-[feature].md` if they exist in the feature directory. Add "Technical Approach" section to the PRD template structure. PRD should be consistent with analysis recommendations unless user explicitly overrides.
  - [ ] 4.2 Modify `generate-tasks.prompt.md`: add instructions to read `architecture-[feature].md` and `design-review-[feature].md` if they exist alongside PRD. Use architecture's build order for parent task ordering, component breakdown for parent task grouping, and Key Decisions Log for subtask alignment.
  - [ ] 4.3 Modify `task-helpers/process-task-list.prompt.md`: add Expert Mode behavioral section based on **Prompt 6 (Expert Mode Switch)**. Bake in: answer as a senior engineer, avoid beginner explanations, be concise but technical, focus on implementation, mention tradeoffs, include pitfalls and best practices. Additionally: follow existing project patterns (check for similar patterns before creating new files), make reasonable choices for underspecified subtasks and document them in the task file.
  - [ ] 4.4 Modify `complete-feature.prompt.md`: add new artifact types to the rename/move step — `research-`, `analysis-`, `architecture-`, `design-review-` all get `completed-` prefix alongside existing `prd-` and `tasks-` files.

## Verification Criteria

How we know the tasks have been successfully implemented:

- [ ] All 5 new `.prompt.md` files exist in the repo root with correct YAML frontmatter
- [ ] Each new prompt specifies the correct output location and naming convention (`tasks/[feature]/[type]-[feature].md`)
- [ ] `research-brief.prompt.md` instructs agent to search the codebase, not just use generic knowledge
- [ ] `analyze-problem.prompt.md` includes the 3-approach comparison table format
- [ ] `design-architecture.prompt.md` includes a build order section and explicitly says NOT to generate tasks
- [ ] `review-and-refine.prompt.md` has distinct Phase A (review) and Phase B (refinement) sections with severity ranking
- [ ] `optimize-prompt.prompt.md` explicitly says NOT to auto-save changes
- [ ] `create-prd.prompt.md` references optional research/analysis artifacts
- [ ] `generate-tasks.prompt.md` references architecture and design-review documents
- [ ] `task-helpers/process-task-list.prompt.md` contains Expert Mode behavioral instructions
- [ ] `complete-feature.prompt.md` lists all new artifact prefixes in the rename step
