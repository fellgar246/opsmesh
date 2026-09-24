# ADR-005: Human approval for side effects

## Status

Accepted

## Context

A draft procurement write changes enterprise state. A model selecting an action is not an authorization. Replaying a changed proposal under an old approval would disconnect the human decision from the arguments that were approved.

## Decision

No agent may mutate enterprise state because a model selected an action. Draft procurement writes require deterministic validation, policy evaluation, and a human approval bound to a hash of the canonical proposal arguments. Any change to those arguments invalidates the approval and requires a new one. An action is allowed only when user permission, agent permission, tool permission, and business policy all allow it. Missing any one of them denies the action. This system does not send purchase orders to real suppliers or move real money.

## Consequences

- Side effects stay behind an approval record tied to exact arguments.
- Re-approval is mandatory after any proposal edit.
- Least privilege is the intersection of the four checks, not a bypass for administrators.
