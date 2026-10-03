# ADR-014: Tool gateway is an adapter

## Status

Accepted

## Context

Specialists need named simulator reads, and those reads already go through a tool port. Giving each specialist its own business-service credential would create a second authorization path. A cloud SDK inside the reorder rules would make the local path depend on a provider.

## Decision

The tool gateway implements the tool port. The live client calls Amazon Bedrock AgentCore Gateway with MCP. A target records the tool name, the argument schema, the permitted agent identities, and the upstream route. The catalog must match the local registry. A mismatch fails before a tool call. The outbound credential is the specialist identity for that tool. The simulator database credential is not part of the specialist configuration. The gateway client is used only when the agentic cloud switch and the tool gateway flag are both on. Otherwise every tool call uses the local registry. A gateway rate limit is a retriable tool failure, capped at the existing task retry limit. Contract checks run against the local registry and a fake gateway. A live gateway check is explicit and stays out of the ordinary quality gate.

## Consequences

- The reorder rules do not import a gateway client.
- A specialist can call only the tools on its allowlist, and a drifted gateway catalog does not run a user request.
- Turning the agentic cloud switch off stops gateway calls without a code change.
