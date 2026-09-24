# ADR-001: Bounded supervisor-specialist architecture

## Status

Accepted

## Context

An operational request can touch inventory, demand, suppliers, finance, and policy. A component that both plans the work and queries operational databases concentrates authority and is difficult to audit. Unbounded delegation makes cost and behavior unpredictable.

## Decision

A supervisor decomposes an operational request into a bounded directed acyclic graph of specialist tasks. Specialists are an allowlist: inventory, demand, supplier, finance, and policy. The supervisor does not query operational databases directly. User text cannot create new agent types. Delegation has no unbounded recursion.

## Consequences

- New specialist kinds require an explicit allowlist change.
- Domain reads and writes stay behind tools and services, outside the supervisor.
- Cyclic or open-ended delegation is rejected before any side effect.
