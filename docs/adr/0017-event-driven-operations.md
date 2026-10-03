# ADR-017: Domain events leave through an outbox

## Status

Accepted

## Context

Workflow, task, proposal, approval, and draft purchase-order changes are already committed in the database. Consumers need those facts for an audit view, notifications, analytics, evaluation sampling, and proposal processing. Publishing inside the business transaction would roll the change back when the bus is down. A consumer that sees the same event twice must not apply the side effect twice.

## Decision

Each state change writes an outbox row in the same transaction. After commit, a worker publishes unpublished rows. A transport failure increments the attempt count and leaves the row unpublished. The business transaction is not rolled back. The local profile uses an in-process bus and does not call a cloud endpoint. Amazon EventBridge is the cloud publisher and Amazon SQS is the cloud consumer, and both are used only when the agentic cloud switch and the event-bus flag are on. The envelope is an event id, type, tenant id, workflow id, occurrence time, and a payload of identifiers and reason codes. Consumers ignore other tenants and record a receipt per event id. A run that finishes waiting for approval publishes `WorkflowCompleted` with that status, because the agent run has finished. A failed task publishes `TaskFailed` and not `TaskCompleted`.

## Consequences

- The golden reorder produces one stable sequence on the local bus, including `WorkflowStarted` and `WorkflowCompleted`.
- Retrying a consumer, or delivering `DraftPOCreated` again, keeps a single side effect and a single draft.
- Local tests do not need cloud credentials. A fake EventBridge client receives the same envelope.
