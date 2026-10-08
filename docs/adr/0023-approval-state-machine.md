# ADR-023: The approval wait is a state machine

## Status

Accepted

## Context

A proposal that needs a person to approve it can sit for the approval lifetime, which is 72 hours. That wait is a business process: approve, reject, or let it expire, then either create one draft and publish a notification, or leave the draft uncreated. Agent planning has already finished by the time the proposal exists. Putting that wait inside a planner would mix a durable process with a model call.

## Decision

The approval wait is a deterministic state machine with states waiting, approved, rejected, expired, draft created, and notified. A matching approval moves it to approved, and the machine reads the live arguments hash before it calls the draft service. A different hash stops the wait and leaves the draft uncreated. A rejection moves it to rejected only after the approval service has recorded `ApprovalRejected`, and it does not create a draft. A timeout uses the approval row's expiry. When that moment has passed, the machine expires the approval, marks the proposal `EXPIRED`, and does not create a draft. The same signal a second time keeps the current state, and a second approve does not create a second draft. The machine calls the approval service and the draft service. The notification consumer on the bus records the draft event.

The local profile runs those transitions in process and publishes on the in-process bus. Amazon Step Functions runs the same states only when `AWS_AGENTIC_FEATURES_ENABLED` and `CLOUD_STATE_MACHINE_ENABLED` are both true. The managed machine is a standard workflow whose wait is 72 hours, matching the approval expiry. The default plan leaves it off. With either flag off, no state-machine client is constructed.

## Consequences

- Approve, reject, and timeout are covered by the local machine against the local bus.
- A repeated approve creates one draft.
- The wait has no path to a model or to the planner.
