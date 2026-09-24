# ADR-002: Local-first simulation

## Status

Accepted

## Context

Cloud agent runtimes and model providers add cost and credentials. Development, tests, and the default product path still need a correct result when those integrations are unavailable.

## Decision

The default execution profile is `local`. In that profile, cloud integrations, the agent runtime, and the model provider stay disabled, and the platform runs local deterministic simulation. The profiles `aws-demo` and `showcase` exist for later optional cloud use. `AWS_AGENTIC_FEATURES_ENABLED=false` forces agentic cloud features off and selects local deterministic simulation, regardless of the other feature flags.

## Consequences

- A clean checkout runs and tests with no cloud credentials.
- Cloud adapters are optional and cannot become a dependency of the local path.
- Turning the master switch off is sufficient to leave deterministic simulation working.
