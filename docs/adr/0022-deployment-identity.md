# ADR-022: Demo infrastructure and deployment identity

## Status

Accepted

## Context

The demo account has to be reproducible from this repository, and a pull request must not spend money or call a live model. A long-lived access key in CI would outlive the demo and would be another secret to rotate.

## Decision

The demo stack is Terraform. Remote state is an encrypted S3 bucket with a DynamoDB lock. GitHub Actions assumes a deploy role through GitHub OIDC, and only workflows that declare the `demo` environment can assume it. Pull-request CI formats and validates the stack and does not apply it. Apply, agent deploy, showcase, and destroy are separate manual workflows. Memory, managed retrieval, managed policy, the daily schedule, and the approval state machine stay off unless the showcase workflow turns them on. The monthly budget is 5 USD, with notifications at 1, 3, and 5 USD. Application quotas remain the hard stop.

## Consequences

- A pull request can pass without cloud credentials.
- Destroying the demo leaves the state bucket and the deploy role in place, so the next apply can run again.
- A default plan does not create the optional showcase modules.
