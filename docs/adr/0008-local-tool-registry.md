# ADR-008: Local tool registry is the tool port

## Status

Accepted

## Context

Specialists need named simulator reads. Each read has a schema and an allowlist of agent types. A later remote gateway should be able to serve the same calls without a second authorization path.

## Decision

Specialists depend on a tool port with three operations: invoke a named tool, list the names an agent may call, and read that tool's argument schema. The local registry is the default implementation. It accepts only read-only tools. URL, SQL, and shell tool types cannot be registered. The supervisor is not on any simulator-tool allowlist. Handlers use the tenant on the task. A tenant argument that disagrees with the task is a denial. Every invocation is stored as a tool execution, including denials and validation failures. A later call with the same arguments hash does not store a second succeeded execution for that task.

## Consequences

- A gateway adapter can implement the same port without changing specialist code that calls it.
- A cross-domain or cross-tenant call is denied, audited, and returns no business payload.
- Invalid arguments fail before the simulator runs, and the error names the field that failed.
