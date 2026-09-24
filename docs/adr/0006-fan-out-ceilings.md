# ADR-006: Fan-out and cost ceilings

## Status

Accepted

## Context

One operational request can fan out into many agent tasks, model calls, and tool calls. Cloud budget alerts do not stop work already in progress. Ceilings have to be application quotas.

## Decision

These ceilings are hard limits. Exceeding a ceiling fails the workflow with an explicit limit error and an audit event. The workflow must not silently truncate a write.

| Ceiling | Value |
| --- | --- |
| Agents per workflow | 6 |
| Tasks per workflow | 12 |
| Delegations per workflow | 10 |
| Supervisor iterations per workflow | 4 |
| Model calls per workflow | 20 |
| Tool calls per workflow | 30 |
| Retries per task | 2 |
| Workflow duration | 180 seconds |
| Output tokens per agent call | 900 |
| Input tokens per model call | 6000 |
| Workflows per day | 15 |

Managed retrieval, long-term memory, and automatic schedules default to off. Infrastructure for the dev environment carries the tags `Project=opsmesh`, `Environment=dev`, `ManagedBy=terraform`, `CostCenter=learning`, and `AutoCleanup=true`.

## Consequences

- A correct workflow that ignores a ceiling is a failed workflow.
- Fan-out stays inside a fixed agent allowlist and a fixed task budget.
- Expensive optional capabilities stay off until a flag enables one of them.
