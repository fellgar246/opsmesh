# ADR-016: Approval is bound to the proposal hash

## Status

Accepted

## Context

A draft purchase order changes simulator state. The policy engine already decides which roles must approve a total. That decision has to be a person, tied to the arguments they saw, and checked again when the draft is submitted. An administrator must not be able to skip the role or the hash.

## Decision

Each required role gets a pending approval that expires 72 hours after it is created. Approve and reject accept the actor and role from the local development headers `X-OpsMesh-Actor` and `X-OpsMesh-Role`. The role must be the required role. Admin is not a substitute. The decision stores the proposal's arguments hash and the actor, and it appends an audit record with the actor, time, proposal, and hash. A second decision on the same row conflicts. Submit advances the draft only when every required role has an approval of the live hash. A mismatch, a rejection, or an expired pending approval denies submit. An expired pending row is marked expired. The advanced order remains inside the simulator.

## Consequences

- A viewer cannot submit the golden proposal. A procurement manager can, after approving the same hash.
- Editing the proposal invalidates the previous approval.
- Manager and finance approvals are checked separately when both apply.
- The console and a direct API call enforce the same decision.
