# ADR-020: Local evaluation scorer

## Status

Accepted

## Context

Prompt, model, and orchestration changes need a score that can be compared across runs. Correctness includes agent selection, tools, facts, policy, approvals, tenant isolation, and ceilings. Efficiency includes completion, model calls, tool calls, latency, and estimated cost. A hosted evaluation service can receive the same fixtures, but it must not become the source of the score, and the default gate must not call a hosted model.

## Decision

Golden workflows live in `evals` as versioned fixtures. The local scorer compares each run to the expected agents, tools, facts, policy outcome, and ceilings. The default gate runs a pinned smoke subset on the local engine. The full job scores every golden workflow and may use recorded traces. Live model evaluation is a separate opt-in and does not run in this profile. The console shows the latest stored report for the active tenant at `/evaluations`. A hosted sink is constructed only when `AWS_AGENTIC_FEATURES_ENABLED` and `HOSTED_EVALUATION_ENABLED` are both true. If that sink fails, the local scores stand. A missing fixture fails the suite.

## Consequences

- Two local smoke runs share the same deterministic scores and do not call a hosted model.
- The local scorer remains the score of record when a hosted sink is enabled.
- Changing an expected approval in a fixture fails that case.
- Integration traces stay out of the default gate.
