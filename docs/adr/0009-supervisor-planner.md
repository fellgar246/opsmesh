# ADR-009: Supervisor planner

## Status

Accepted

## Context

A reorder, supplier delay, budget plan, or incident needs a known set of specialists before any of them run. The component that chooses those specialists must not also query operational databases. A model can propose the graph, but an invalid graph must never become runnable work.

## Decision

The supervisor returns a plan with tasks, dependencies, and an English reason. Each task names an allowlisted specialist and a typed input. Validation rejects unknown agents, cycles, missing task ids, duplicate equivalent tasks, writes, database tools, and plans above 6 agents, 12 tasks, or 10 delegations. Supervisor iterations on one workflow stop at 4.

The local profile uses a deterministic planner for reorder, supplier delay, and budget plan. A model planner is selected only when the model provider flag is on. It emits the same document and passes the same validator. Prose around the JSON is discarded. The validated plan is stored on the workflow run. The planner receives the request only. It has no database client and no simulator tool.

## Consequences

- Fan-out can trust the graph before it executes specialists.
- User text cannot add an agent type or a database tool.
- A rejected plan is not stored as runnable tasks.
- Incident analysis is planned as a read-only graph. Execution of that graph is separate.
