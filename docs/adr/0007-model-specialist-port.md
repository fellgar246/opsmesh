# ADR-007: Model specialists sit behind a port

## Status

Accepted

## Context

Specialists need to explain evidence with a model, but stock, price, budget, lead time, and approval decisions are operational facts. A model response cannot be the source of those facts, and local development has to keep working when the model provider is off.

## Decision

Each specialist is reached through the `AgentClient` port. A model adapter implements that port: it reads facts from tools and the deterministic policy service, asks a model port for a JSON envelope, and rejects output that is not valid for the result schema or that exceeds 900 output tokens. Requests send at most 6000 input tokens and 900 output tokens. Numeric operational facts and approval booleans are copied from the tools after the model returns. Contradictory policy prose is discarded. The live model provider is Amazon Bedrock, behind the model port. When the model provider flag is off, the same workflow uses the local stubs. Stored results record the model id and prompt version only when a model call ran.

## Consequences

- The coordinator keeps accepting validated envelopes and does not depend on a model SDK.
- Disabling the model provider leaves the deterministic reorder result unchanged.
- A tool failure or an invalid model payload does not write invented stock, prices, or approval flags.
