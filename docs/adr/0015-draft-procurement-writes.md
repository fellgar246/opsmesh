# ADR-015: Draft procurement writes stay local

## Status

Accepted

## Context

A feasible reorder proposal needs a purchase order before a person can approve it. That write changes simulator state. Replaying it must not create a second order, and a specialist must not be able to perform it. The order also must not leave the simulator.

## Decision

The local registry adds `procurement.create_draft_purchase_order` and `procurement.submit_purchase_order_for_approval`. Only the procurement executor may call them. Create stores one draft when the proposal is feasible and the SKU, supplier, quantity, and unit cost match the proposal. Both tools require an idempotency key and store the arguments hash with the result. The same key and arguments return the stored order. The same key with different arguments fails and leaves the stored order unchanged. Submit moves a draft to waiting for approval. When the extended cost requires approval, submit sets the proposal to pending approval. It never marks the proposal approved and it never contacts a supplier. These tools stay off the tool gateway catalog. A gateway session runs them on the local registry.

## Consequences

- Repeating a draft call returns one purchase order.
- A missing proposal, an infeasible proposal, or arguments that differ from the proposal do not write.
- Inventory, demand, supplier, finance, policy, and the supervisor cannot call the write tools.
- Turning the tool gateway on does not send a draft to a supplier.
