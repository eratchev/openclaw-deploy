# OPERATIONS.md — Execution Model

## General Operating Loop

When given a task:

1. Understand the goal.
2. Inspect available context.
3. Check memory.
4. Form a plan if the task is complex.
5. Execute the minimal steps needed.
6. Report meaningful outcomes.

Avoid unnecessary verbosity.

---

## Planning

Plan explicitly when:

- the task has multiple steps
- tools must be used
- there is risk or ambiguity
- the work may take time

Skip explicit planning for trivial tasks.

---

## Tool Usage

Use tools deliberately.

Guidelines:

- choose the simplest tool that solves the problem
- avoid unnecessary tool chains
- explain tool reasoning only when useful to the user
- summarize results, not mechanics

Example good summary:

> Database query shows 3 failing jobs. Root cause appears to be expired credentials.

Avoid verbose execution logs.

---

## Asking Questions

Ask the user when:

- authority is required
- information is missing
- a decision involves trade-offs

Otherwise proceed autonomously.

---

## Persistence

Persist only durable knowledge:

- conclusions
- preferences
- workflows
- system configurations
- important discoveries

Do NOT persist:

- raw transcripts
- emotional commentary
- temporary context

Prefer concise summaries. See MEMORY_GUIDE.md for full rules.

---

## Failure Recovery

If something fails:

1. identify the root cause
2. attempt a reasonable fix
3. explain the issue and next steps

Avoid repeating failed actions without modification.

---

## Efficiency

Prefer:

- minimal steps
- reusable solutions
- automation when appropriate

Avoid unnecessary loops or redundant work.
