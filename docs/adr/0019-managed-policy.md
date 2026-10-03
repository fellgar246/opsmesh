# ADR-019: Managed policy is a second vote

## Status

Accepted

## Context

Purchase thresholds, supplier eligibility, quantity, and human approval are deterministic rules. A managed policy service can add a further restriction on agent and tool operations, but it must not become a second copy of those rules or a way to loosen them. The local path has to keep working when the managed service is off.

## Decision

The local policy engine remains the source of truth. An optional adapter adds a second vote on agent runs and tool calls, and only when `AWS_AGENTIC_FEATURES_ENABLED` and `CLOUD_POLICY_ENABLED` are both true. Otherwise no managed policy client is constructed. The effective decision is the conjunction of the local vote and, when enabled, the managed vote: either deny is a deny. The adapter does not change proposal quantities. It cannot permit an unapproved supplier or a missing human approval. The managed client targets Amazon Bedrock AgentCore Policy and stays outside the domain package. If the adapter does not return a decision, the gated operation fails closed.

## Consequences

- A flag-off reorder matches the local engine and does not call a policy endpoint.
- A managed allow cannot override a local deny.
- A managed deny blocks a tool the local allowlist would permit.
- An unavailable adapter blocks the operation instead of skipping the check.
