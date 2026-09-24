# ADR-003: Tool protocol and agent protocol stay separate

## Status

Accepted

## Context

Tools and specialist agents are different kinds of collaborators. Tools perform a bounded operation and return data. Agents gather and explain evidence. Using one protocol for both hides that difference and makes authorization harder to enforce.

## Decision

Tools are exposed through a tool protocol (MCP-style tool calls). Specialist agents communicate through an agent protocol over HTTP with a versioned result envelope. Unstructured prose is not the contract between the supervisor, specialists, or tools. Results that fail schema validation are rejected.

## Consequences

- Tool implementations and agent runtimes can change independently.
- Callers depend on typed envelopes, not on vendor SDK shapes.
- A model response cannot stand in for a tool result.
