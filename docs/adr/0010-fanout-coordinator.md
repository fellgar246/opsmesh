# ADR-010: Fan-out coordinator

## Status

Accepted

## Context

A validated plan names specialists that can run together and specialists that must wait. Writing every task status only at the end hides a failure from a restarted process, and a missing supplier fact must not become a purchase.

## Decision

The coordinator executes one validated plan. Independent tasks are marked running and started together. A task starts only when every dependency is completed. Skipped, failed, and cancelled dependencies do not unblock downstream work. Each status change is stored before the coordinator reads it back. Allowed changes are an explicit map: pending to ready, cancelled, or skipped; ready to running, cancelled, or skipped; running to completed, failed, or cancelled; failed back to ready for a retry.

A task retries at most twice. The retry count is stored on the task. Each task times out, and the workflow stops at 180 seconds with reason `workflow_timeout`. In-flight results after a timeout are discarded. Model calls stop at 20 and tool calls stop at 30; the next task is not started past either ceiling. When the required reorder facts are present, the coordinator calls the deterministic reorder function. Missing facts finish as `missing_evidence` and do not create a proposal.

## Consequences

- Sibling results stay after one specialist fails.
- A restarted reader sees the same task statuses that were committed at the failure.
- The workflow cannot keep delegating after a ceiling or the 180-second deadline.
