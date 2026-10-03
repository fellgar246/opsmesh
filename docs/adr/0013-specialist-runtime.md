# ADR-013: Specialist runtime is an adapter

## Status

Accepted

## Context

Specialists need a managed runtime for a cloud demo, and operators need to move one specialist at a time. The reorder coordinator already accepts a versioned result envelope. A second workflow engine, or a cloud SDK inside the coordinator, would make the local path depend on a provider.

## Decision

Each specialist is reached through the `AgentClient` port. A remote client implements that port for Amazon Bedrock AgentCore Runtime. An endpoint records the agent type, the transport (`local` or `remote`), and the runtime identifier. Selection is per specialist and follows the conversion order: policy, inventory, supplier, finance, then demand. The remote client is used only when the agentic cloud switch and the cloud agent runtime flag are both on, and the specialist is listed. Otherwise every specialist uses the local client.

The runtime SDK stays inside the runtime adapter. The outbound call carries that specialist's agent identity and does not attach an end-user credential. The call uses the same task timeout as local execution. A response that adds fields outside the result schema fails the task with empty facts. A transport failure fails that task with a transport warning and leaves sibling results in place. Contract checks run against the local client and a fake runtime transport. A live runtime check is explicit and stays out of the ordinary quality gate.

## Consequences

- The coordinator and the reorder rules do not import a cloud SDK.
- Pointing one specialist at a runtime leaves the others on the local client, and the proposal rules stay the same when the remote envelope is valid.
- Turning the agentic cloud switch off stops remote calls without a code change.
