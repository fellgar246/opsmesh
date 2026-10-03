# ADR-018: Verified tenant and role claims

## Status

Accepted

## Context

Business routes, tool calls, and agent calls need a tenant and a role that the caller cannot invent. Development headers are not a trust boundary once more than one person uses the console. A tenant administrator must not become an operator for every tenant. Agent runtime calls and gateway calls already use a specialist identity, and that identity has to stay separate from the person's token.

## Decision

Business routes require a bearer token. The local execution profile signs and verifies that token with a development issuer and does not contact a user pool. Any other execution profile verifies Amazon Cognito access and ID tokens. The tenant id and the role are read from the verified claims. Cognito group names match the role identifiers. A path or tool argument that names a different tenant is not used: a route responds not found, and a tool call is denied. Starting a workflow requires the operations analyst role. An approval requires the role named on that approval. A platform role is cross-tenant for reads only when `PLATFORM_ROLE` is set to a name outside the tenant role set. The specialist's runtime identity and the gateway credential are not the end-user token. An action is allowed only when the user, the agent, the tool, and the policy all allow it.

## Consequences

- Local tests sign tokens in process and pass without a hosted user pool.
- The console obtains a local session and sends it on each request.
- A viewer cannot start a workflow or approve a proposal. A tenant admin cannot read another tenant.
